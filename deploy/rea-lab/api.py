"""Offline REA Lab analysis API. Never executes a submitted application.

Only one static-analysis job at a time; input is a named, read-only artifact.
The API is reachable only through a loopback ingress and requires a private token.
Results are evidence, not proof that any code path executed.
"""
from __future__ import annotations

import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid
import zipfile

VAULT = Path(os.environ.get("REA_LAB_VAULT", "/vault"))
RESULTS = Path(os.environ.get("REA_LAB_RESULTS", "/results"))
TOKEN_FILE = Path(os.environ.get("REA_LAB_TOKEN_FILE", "/run/secrets/rea_lab_token"))
JADX = os.environ.get("REA_LAB_JADX", "/opt/jadx/bin/jadx")
GHIDRA = os.environ.get("REA_LAB_GHIDRA", "")
MAX_FILE_BYTES = 512 * 1024 * 1024  # Matches the existing managed vault.
MAX_UNPACK_BYTES = 1536 * 1024 * 1024
MIN_FREE_BYTES = 4 * 1024 * 1024 * 1024
TIMEOUT_SECONDS = 45 * 60
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
JOB_ID = re.compile(r"[a-f0-9]{32}\Z")
EXTS = (".apk", ".xapk", ".aab", ".jar", ".zip", ".asar")
MODES = ("inventory", "android", "native")
_LOCK = threading.Lock()
_ACTIVE: str | None = None


class LabError(Exception):
    pass


def validate_input(filename):
    if not isinstance(filename, str) or not NAME.fullmatch(filename) or filename.startswith("."):
        raise LabError("invalid_filename")
    if not filename.lower().endswith(EXTS):
        raise LabError("unsupported_extension")
    if VAULT.is_symlink() or not VAULT.is_dir():
        raise LabError("vault_unavailable")
    path = VAULT / filename
    try:
        item = path.lstat()
    except OSError as exc:
        raise LabError("artifact_not_found") from exc
    if not stat.S_ISREG(item.st_mode) or item.st_nlink != 1 or not 0 < item.st_size <= MAX_FILE_BYTES:
        raise LabError("invalid_artifact")
    return path


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    temp.replace(path)


def read_job(job_id):
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise LabError("invalid_job_id")
    file = RESULTS / job_id / "status.json"
    if not file.is_file() or file.is_symlink():
        raise LabError("job_not_found")
    data = json.loads(file.read_text(encoding="utf-8"))
    if data.get("state") == "running" and job_id != _ACTIVE:
        data["state"] = "interrupted"
    return data


def safe_members(archive):
    total = 0
    output = []
    for entry in archive.infolist():
        if entry.is_dir():
            continue
        name = entry.filename
        parts = name.split("/")
        if (not name or name.startswith("/") or "\\" in name or any(p in ("", ".", "..") for p in parts)
                or (entry.external_attr >> 16) & 0o170000 == 0o120000):
            raise LabError("unsafe_archive_entry")
        if entry.file_size < 0 or entry.file_size > MAX_UNPACK_BYTES:
            raise LabError("archive_entry_too_large")
        total += entry.file_size
        if total > MAX_UNPACK_BYTES:
            raise LabError("archive_unpacked_size_exceeded")
        output.append(entry)
    return output


def archive_inventory(path):
    if not zipfile.is_zipfile(path):
        return {"format": "binary", "entries": [], "entries_total": 0}
    with zipfile.ZipFile(path) as archive:
        entries = safe_members(archive)
        return {
            "format": "zip",
            "entries_total": len(entries),
            "entries": [{"path": item.filename, "bytes": item.file_size}
                        for item in entries[:300]],
            "entries_truncated": len(entries) > 300,
        }


def extract_selected(archive, item, destination):
    # Never use extractall, and never trust an archive entry as a filesystem path.
    with archive.open(item) as source, destination.open("xb") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)
    if destination.stat().st_size != item.file_size:
        raise LabError("extraction_size_mismatch")


def select_apk(path, workspace):
    if path.suffix.lower() not in (".xapk", ".zip"):
        return path, None
    with zipfile.ZipFile(path) as archive:
        members = safe_members(archive)
        apks = [m for m in members if m.filename.lower().endswith(".apk")]
        if not apks:
            raise LabError("archive_contains_no_apk")
        # Prefer explicit base; for XAPK names such as com.ringapp.apk use largest.
        base = sorted(apks, key=lambda m: (
            0 if Path(m.filename).name in ("base.apk", "base-master.apk") else 1,
            -m.file_size, m.filename,
        ))[0]
        dest = workspace / "base.apk"
        extract_selected(archive, base, dest)
        return dest, base.filename


def collect_native(path, workspace):
    extracted = []
    def visit_zip(source, depth=0):
        if depth > 1:
            return
        with zipfile.ZipFile(source) as archive:
            for member in safe_members(archive):
                low = member.filename.lower()
                if low.endswith(".so") and len(extracted) < 100:
                    out = workspace / ("lib_%03d.so" % len(extracted))
                    extract_selected(archive, member, out)
                    extracted.append((member.filename, out))
                elif depth == 0 and low.endswith(".apk"):
                    intermediate = workspace / ("nested_%03d.apk" % len(extracted))
                    extract_selected(archive, member, intermediate)
                    visit_zip(intermediate, depth + 1)
                    intermediate.unlink(missing_ok=True)
    if zipfile.is_zipfile(path):
        visit_zip(path)
    return extracted


def run_native(path, workspace):
    libraries = collect_native(path, workspace)
    records = []
    for name, lib in libraries:
        try:
            run = subprocess.run(["readelf", "-h", "-d", str(lib)], text=True,
                                 capture_output=True, timeout=10, check=False)
            records.append({"entry": name, "sha256": digest(lib),
                            "elf": run.returncode == 0,
                            "headers": run.stdout[:2800]})
        except (OSError, subprocess.TimeoutExpired):
            records.append({"entry": name, "error": "readelf_unavailable"})
    return {"libraries": records, "total_libraries": len(libraries),
            "native_decompilation": "unavailable" if not GHIDRA else "separate_provider_required"}


def run_android(path, workspace, out):
    apk, nested_entry = select_apk(path, workspace)
    source_dir = out / "sources"
    source_dir.mkdir()
    command = [JADX, "--threads-count", "2", "--output-dir", str(source_dir), str(apk)]
    env = dict(os.environ)
    env["JAVA_TOOL_OPTIONS"] = "-Xmx1536m"
    try:
        p = subprocess.run(command, cwd=workspace, env=env, capture_output=True,
                           text=True, timeout=TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired as exc:
        raise LabError("jadx_timeout") from exc
    except OSError as exc:
        raise LabError("jadx_not_available") from exc
    file_count = sum(1 for p in source_dir.rglob("*") if p.is_file() and not p.is_symlink())
    return {"base_apk_entry": nested_entry, "jadx_exit_code": p.returncode,
            "decompiled_files": file_count, "log_tail": (p.stdout + p.stderr)[-3000:],
            "warning": "Decompiled source is approximate and is not original source."}


def run_job(job_id, filename, mode):
    global _ACTIVE
    path = RESULTS / job_id
    data = read_job(job_id)
    try:
        source = validate_input(filename)
        if shutil.disk_usage(RESULTS).free < MIN_FREE_BYTES:
            raise LabError("insufficient_free_disk")
        data.update({"sha256": digest(source), "bytes": source.stat().st_size})
        write_json(path / "status.json", data)
        with tempfile.TemporaryDirectory(dir="/work", prefix="rea-lab-") as tmp:
            workspace = Path(tmp)
            if mode == "android":
                details = run_android(source, workspace, path)
            elif mode == "native":
                details = run_native(source, workspace)
            else:
                details = archive_inventory(source)
        data["state"] = "completed"
        data["finished_epoch"] = int(time.time())
        write_json(path / "report.json", {
            "artifact": filename, "artifact_sha256": data["sha256"],
            "mode": mode, "evidence_type": "static_artifact",
            "observations": details,
            "unknowns": ["Runtime execution, user behavior and network traffic are not observed."],
        })
    except Exception as exc:
        data["state"] = "failed"
        data["error"] = str(exc) if isinstance(exc, LabError) else "analysis_failed"
        data["finished_epoch"] = int(time.time())
    finally:
        write_json(path / "status.json", data)
        with _LOCK:
            if _ACTIVE == job_id:
                _ACTIVE = None


def start_job(filename, mode):
    global _ACTIVE
    validate_input(filename)
    if mode not in MODES:
        raise LabError("invalid_mode")
    with _LOCK:
        if _ACTIVE is not None:
            raise LabError("analysis_busy")
        job_id = uuid.uuid4().hex
        folder = RESULTS / job_id
        folder.mkdir(mode=0o700)
        write_json(folder / "status.json",
                   {"job_id": job_id, "artifact": filename, "mode": mode,
                    "state": "running", "started_epoch": int(time.time())})
        _ACTIVE = job_id
        threading.Thread(target=run_job, args=(job_id, filename, mode), daemon=True).start()
    return {"ok": True, "job_id": job_id, "state": "running"}


def search_job(job_id, term):
    data = read_job(job_id)
    if data["state"] != "completed" or data["mode"] != "android":
        raise LabError("search_requires_completed_android_job")
    if not isinstance(term, str) or not 2 <= len(term) <= 80 or any(ord(c) < 32 for c in term):
        raise LabError("invalid_query")
    root = RESULTS / job_id / "sources"
    matches = []
    scanned = 0
    for p in root.rglob("*"):
        if not p.is_file() or p.is_symlink() or p.suffix.lower() not in (".java", ".xml", ".json", ".kt"):
            continue
        if p.stat().st_size > 2 * 1024 * 1024:
            continue
        scanned += 1
        with p.open(encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, 1):
                if term.casefold() in line.casefold():
                    matches.append({"file": str(p.relative_to(root)), "line": lineno,
                                    "excerpt": line.strip()[:240]})
                    if len(matches) >= 50:
                        return {"ok": True, "matches": matches, "truncated": True,
                                "files_scanned": scanned}
    return {"ok": True, "matches": matches, "truncated": False, "files_scanned": scanned}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def respond(self, status, obj):
        raw = json.dumps(obj, ensure_ascii=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def auth(self):
        expected = TOKEN_FILE.read_text(encoding="ascii").strip()
        supplied = self.headers.get("X-REA-LAB-TOKEN", "")
        return bool(expected) and hmac.compare_digest(supplied, expected)

    def dispatch(self):
        if not self.auth():
            self.respond(403, {"ok": False, "error": "unauthorized"})
            return
        parsed = urlsplit(self.path)
        parts = parsed.path.strip("/").split("/")
        if self.command == "GET" and parts == ["capabilities"]:
            self.respond(200, {"ok": True, "modes": list(MODES), "active_job": _ACTIVE,
                               "max_file_bytes": MAX_FILE_BYTES, "max_unpacked_bytes": MAX_UNPACK_BYTES,
                               "max_parallel_jobs": 1, "jadx": Path(JADX).is_file(),
                               "ghidra": bool(GHIDRA) and Path(GHIDRA).exists(),
                               "runtime_execution": False})
        elif self.command == "POST" and parts == ["jobs"]:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 4096:
                raise LabError("invalid_request_length")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict) or set(data) != {"filename", "mode"}:
                raise LabError("invalid_fields")
            self.respond(202, start_job(data["filename"], data["mode"]))
        elif self.command == "GET" and len(parts) >= 2 and parts[0] == "jobs":
            job_id = parts[1]
            if len(parts) == 2:
                self.respond(200, {"ok": True, **read_job(job_id)})
            elif len(parts) == 3 and parts[2] == "report":
                if read_job(job_id)["state"] != "completed":
                    raise LabError("report_not_ready")
                self.respond(200, {"ok": True, **json.loads((RESULTS / job_id / "report.json").read_text())})
            elif len(parts) == 3 and parts[2] == "search":
                qs = parse_qs(parsed.query)
                if set(qs) != {"q"} or len(qs["q"]) != 1:
                    raise LabError("invalid_query")
                self.respond(200, search_job(job_id, qs["q"][0]))
            else:
                raise LabError("not_found")
        else:
            raise LabError("not_found")

    def do_GET(self):
        self.safe_dispatch()

    def do_POST(self):
        self.safe_dispatch()

    def safe_dispatch(self):
        try:
            self.dispatch()
        except (LabError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            code = str(exc) if isinstance(exc, LabError) else "invalid_request"
            self.respond(400, {"ok": False, "error": code})
        except (OSError, UnicodeError):
            self.respond(500, {"ok": False, "error": "internal_io_error"})


def main():
    RESULTS.mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer(("0.0.0.0", 8766), Handler).serve_forever()


if __name__ == "__main__":
    main()
