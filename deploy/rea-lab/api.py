"""Offline REA Lab analysis API. Never executes a submitted application.

Only one static-analysis job at a time; input is a named, read-only artifact.
The API is reachable only through a loopback ingress and requires a private token.
Results are evidence, not proof that any code path executed.
"""
from __future__ import annotations

import hashlib
import importlib.util
from functools import lru_cache
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
from urllib import request as urllib_request

VAULT = Path(os.environ.get("REA_LAB_VAULT", "/vault"))
RESULTS = Path(os.environ.get("REA_LAB_RESULTS", "/results"))
TOKEN_FILE = Path(os.environ.get("REA_LAB_TOKEN_FILE", "/run/secrets/rea_lab_token"))
JADX = os.environ.get("REA_LAB_JADX", "/opt/jadx/bin/jadx")
GHIDRA = os.environ.get("REA_LAB_GHIDRA", "")
MAX_FILE_BYTES = 512 * 1024 * 1024  # Matches the existing managed vault.
MAX_UNPACK_BYTES = 1536 * 1024 * 1024
MIN_FREE_BYTES = 4 * 1024 * 1024 * 1024
TIMEOUT_SECONDS = 45 * 60
# 1.75 GiB heap leaves approximately 508 MiB below the 2300m container cap.
JADX_HEAP_MIB = 1792
# Large base APKs use isolated DEX passes instead of one large JVM load.
DEX_FIRST_APK_BYTES = 128 * 1024 * 1024
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
JOB_ID = re.compile(r"[a-f0-9]{32}\Z")
EXTS = (".apk", ".xapk", ".aab", ".jar", ".zip", ".asar")
MODES = ("inventory", "android", "native")
WEB_CAPTURE_URL = os.environ.get("REA_WEB_CAPTURE_URL", "http://web_capture:8767/capture")
_LOCK = threading.Lock()
_ACTIVE: str | None = None
_INDEX_ACTIVE: str | None = None


@lru_cache(maxsize=1)
def intelligence():
    """Load only the installed sibling module (works in offline test imports)."""
    path = Path(__file__).with_name("intelligence.py")
    spec = importlib.util.spec_from_file_location("rea_lab_intelligence", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def web_intelligence():
    """Load locally installed offline public-web code analyzer."""
    path = Path(__file__).with_name("web_intelligence.py")
    spec = importlib.util.spec_from_file_location("rea_web_intelligence", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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



def run_android_per_dex(apk, workspace, source_dir, nested_entry, env, progress=None):
    """Bounded per-DEX JADX passes; returns None if the APK has no root DEX."""
    if not zipfile.is_zipfile(apk):
        return None
    with zipfile.ZipFile(apk) as archive:
        members = safe_members(archive)
        dex = [item for item in members
               if re.fullmatch(r"classes(?:[0-9]+)?\.dex", item.filename)]
        if not dex:
            return None

        def dex_order(item):
            suffix = item.filename[len("classes"):-len(".dex")]
            return int(suffix) if suffix else 1

        dex.sort(key=lambda item: (dex_order(item), item.filename))
        records = []
        if progress:
            progress({"state": "running", "strategy": "individual_dex",
                      "dex_total": len(dex), "dex_finished": 0})
        begin = time.monotonic()
        for index, item in enumerate(dex, 1):
            if progress:
                progress({"state": "running", "strategy": "individual_dex",
                          "dex_total": len(dex), "dex_finished": len(records),
                          "current_dex": item.filename})
            remaining = TIMEOUT_SECONDS - (time.monotonic() - begin)
            if remaining <= 0:
                records.append({"entry": item.filename, "error": "time_budget_exhausted"})
                break
            dex_file = workspace / f"dex_{index:03d}.dex"
            output_dir = source_dir / f"dex_{index:03d}"
            extract_selected(archive, item, dex_file)
            command = [JADX, "--threads-count", "1", "--no-res",
                       "--output-dir", str(output_dir), str(dex_file)]
            try:
                result = subprocess.run(
                    command, cwd=workspace, env=env, capture_output=True,
                    text=True, timeout=remaining, check=False,
                )
                log = result.stdout + result.stderr
                file_count = sum(1 for file in output_dir.rglob("*")
                                 if file.is_file() and not file.is_symlink())
                records.append({
                    "entry": item.filename,
                    "exit_code": result.returncode,
                    "decompiled_files": file_count,
                    "out_of_memory": "java.lang.OutOfMemoryError" in log,
                    "log_tail": log[-1000:],
                })
            except subprocess.TimeoutExpired:
                records.append({"entry": item.filename, "error": "jadx_timeout"})
                break
            finally:
                dex_file.unlink(missing_ok=True)
                if progress:
                    progress({"state": "running", "strategy": "individual_dex",
                              "dex_total": len(dex), "dex_finished": len(records),
                              "current_dex": None,
                              "files_recovered": sum(r.get("decompiled_files", 0) for r in records)})

    file_count = sum(record.get("decompiled_files", 0) for record in records)
    succeeded = len(records) == len(dex) and all(
        record.get("exit_code") == 0 for record in records
    )
    return {
        "base_apk_entry": nested_entry,
        "analysis_strategy": "individual_dex",
        "jadx_exit_code": 0 if succeeded else 1,
        "jadx_heap_mib": JADX_HEAP_MIB,
        "out_of_memory": any(record.get("out_of_memory", False) for record in records),
        "decompiled_files": file_count,
        "dex_total": len(dex),
        "dex_results": records,
        "warning": "DEX-level source is approximate; Android resources are not decoded in this mode.",
    }


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


def run_android(path, workspace, out, progress=None):
    apk, nested_entry = select_apk(path, workspace)
    source_dir = out / "sources"
    source_dir.mkdir()
    # The unprivileged user's passwd home may be /nonexistent, while the
    # container root is read-only. Give JADX and its JVM a per-job writable
    # home without relaxing the filesystem isolation.
    home = workspace / "jadx-home"
    xdg_dirs = {
        "XDG_CONFIG_HOME": home / ".config",
        "XDG_CACHE_HOME": home / ".cache",
        "XDG_DATA_HOME": home / ".local" / "share",
        "XDG_STATE_HOME": home / ".local" / "state",
    }
    home.mkdir(mode=0o700)
    for directory in xdg_dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
    command = [JADX, "--threads-count", "2", "--output-dir", str(source_dir), str(apk)]
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.update({name: str(directory) for name, directory in xdg_dirs.items()})
    env["JAVA_TOOL_OPTIONS"] = f"-Xmx{JADX_HEAP_MIB}m -Duser.home={home}"
    metadata = intelligence().apk_metadata(apk)
    if apk.stat().st_size >= DEX_FIRST_APK_BYTES:
        result = run_android_per_dex(apk, workspace, source_dir, nested_entry, env, progress)
        if result is not None:
            result["static_inventory"] = metadata
            return result
    try:
        p = subprocess.run(command, cwd=workspace, env=env, capture_output=True,
                           text=True, timeout=TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired as exc:
        raise LabError("jadx_timeout") from exc
    except OSError as exc:
        raise LabError("jadx_not_available") from exc
    file_count = sum(1 for p in source_dir.rglob("*") if p.is_file() and not p.is_symlink())
    logs = p.stdout + p.stderr
    return {"base_apk_entry": nested_entry, "jadx_exit_code": p.returncode,
            "jadx_heap_mib": JADX_HEAP_MIB,
            "out_of_memory": "java.lang.OutOfMemoryError" in logs,
            "static_inventory": metadata,
            "decompiled_files": file_count, "log_tail": logs[-3000:],
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
                details = run_android(source, workspace, path,
                                      lambda p: write_json(path / "progress.json", p))
            elif mode == "native":
                details = run_native(source, workspace)
            else:
                details = archive_inventory(source)
        data["state"] = "completed"
        if mode == "android":
            count = details.get("decompiled_files", 0)
            code = details.get("jadx_exit_code")
            data["analysis_result"] = ("clean" if count > 0 and code == 0
                                       else "partial" if count > 0 else "failed")
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



def run_web_job(job_id, target):
    """Snapshot through separately networked sidecar; never add worker egress."""
    global _ACTIVE
    folder = RESULTS / job_id
    data = read_job(job_id)
    try:
        if shutil.disk_usage(RESULTS).free < MIN_FREE_BYTES:
            raise LabError("insufficient_free_disk")
        body = json.dumps({"url": target}).encode("utf-8")
        req = urllib_request.Request(
            WEB_CAPTURE_URL, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "X-REA-LAB-TOKEN": TOKEN_FILE.read_text(encoding="ascii").strip()},
        )
        try:
            with urllib_request.urlopen(req, timeout=220) as response:
                blob = response.read(15_000_001)
            if len(blob) > 15_000_000:
                raise LabError("web_capture_too_large")
            capture = json.loads(blob)
        except (OSError, ValueError):
            raise LabError("web_capture_unavailable_or_rejected") from None
        if not isinstance(capture, dict) or capture.get("ok") is not True:
            raise LabError("web_capture_rejected")
        obs = web_intelligence().analyze_and_save(folder, capture)
        sig = hashlib.sha256("".join(x["sha256"] for x in obs["client_assets"]).encode()).hexdigest()
        data.update({"state": "completed",
                     "analysis_result": "partial" if obs["asset_errors"] else "clean_static_snapshot",
                     "sha256": sig, "finished_epoch": int(time.time())})
        write_json(folder / "report.json", {
            "artifact": data["artifact"], "artifact_sha256": sig, "mode": "web",
            "evidence_type": "public_web_static_snapshot",
            "observations": obs,
            "unknowns": ["Private backend/model identity and runtime behavior are unobserved."],
        })
    except Exception as exc:
        data.update({"state": "failed",
                     "error": str(exc) if isinstance(exc, LabError) else "web_analysis_failed",
                     "finished_epoch": int(time.time())})
    finally:
        write_json(folder / "status.json", data)
        with _LOCK:
            if _ACTIVE == job_id:
                _ACTIVE = None


def start_web_job(target):
    """One immutable public web snapshot under the existing job concurrency gate."""
    global _ACTIVE
    if not isinstance(target, str) or not 12 <= len(target) <= 2048:
        raise LabError("invalid_web_url")
    parsed = urlsplit(target)
    try:
        port = parsed.port
    except ValueError:
        raise LabError("invalid_web_url") from None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or port not in (None, 443)
            or any(ord(c) < 32 or ord(c) == 127 for c in target)):
        raise LabError("public_https_url_required")
    # The sidecar independently blocks private DNS, redirects, credentials and IP URLs.
    display = "https://" + parsed.hostname + (parsed.path or "/")
    with _LOCK:
        if _ACTIVE is not None or _INDEX_ACTIVE is not None:
            raise LabError("analysis_busy")
        job_id = uuid.uuid4().hex
        folder = RESULTS / job_id
        folder.mkdir(mode=0o700)
        write_json(folder / "status.json", {
            "job_id": job_id, "artifact": display[:260], "mode": "web",
            "state": "running", "started_epoch": int(time.time()),
        })
        _ACTIVE = job_id
        threading.Thread(target=run_web_job, args=(job_id, target), daemon=True).start()
    return {"ok": True, "job_id": job_id, "state": "running", "mode": "web"}


def index_state(job_id):
    job = read_job(job_id)
    if job["mode"] not in ("android", "web") or job["state"] != "completed":
        raise LabError("index_requires_completed_static_job")
    root = RESULTS / job_id
    if (root / "intelligence.sqlite").is_file():
        status_file = root / "index.json"
        if status_file.is_file():
            return {"ok": True, **json.loads(status_file.read_text(encoding="utf-8"))}
        return {"ok": True, "state": "ready"}
    status_file = root / "index.json"
    if status_file.is_file():
        return {"ok": True, **json.loads(status_file.read_text(encoding="utf-8"))}
    return {"ok": True, "state": "not_started"}


def _build_index(job_id):
    global _INDEX_ACTIVE
    root = RESULTS / job_id
    try:
        intelligence().build_index(root, lambda p: write_json(root / "index.json", p))
    except Exception:
        write_json(root / "index.json", {"state": "failed", "error": "index_failed"})
    finally:
        with _LOCK:
            if _INDEX_ACTIVE == job_id:
                _INDEX_ACTIVE = None


def start_index(job_id):
    global _INDEX_ACTIVE
    status = index_state(job_id)
    if status["state"] == "ready":
        return status
    with _LOCK:
        if _INDEX_ACTIVE == job_id:
            return {"ok": True, "state": "building", "job_id": job_id}
        if _ACTIVE is not None or _INDEX_ACTIVE is not None:
            raise LabError("analysis_busy")
        _INDEX_ACTIVE = job_id
        write_json(RESULTS / job_id / "index.json", {"state": "building", "files_indexed": 0})
        threading.Thread(target=_build_index, args=(job_id,), daemon=True).start()
    return {"ok": True, "state": "building", "job_id": job_id}


def compact_summary(job_id, offset=0, limit=12):
    job = read_job(job_id)
    if job["state"] != "completed":
        raise LabError("report_not_ready")
    report_path = RESULTS / job_id / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return {"job_id": job_id, **(web_intelligence().compact_web_report(report, offset, limit)
                                  if job["mode"] == "web" else intelligence().compact_report(report, offset, limit))}


def ready_index(job_id):
    state = index_state(job_id)
    if state["state"] != "ready":
        raise LabError("index_not_ready")
    return RESULTS / job_id


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
    if data["state"] != "completed" or data["mode"] not in ("android", "web"):
        raise LabError("search_requires_completed_static_job")
    if not isinstance(term, str) or not 2 <= len(term) <= 80 or any(ord(c) < 32 for c in term):
        raise LabError("invalid_query")
    root = RESULTS / job_id / "sources"
    matches = []
    scanned = 0
    for p in root.rglob("*"):
        if not p.is_file() or p.is_symlink() or p.suffix.lower() not in (".java", ".xml", ".json", ".kt", ".js", ".html", ".txt"):
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



def source_excerpt(job_id, filename, first_line):
    """Bounded, path-safe read of a JADX-produced source file, never input vault."""
    from pathlib import PurePosixPath
    job = read_job(job_id)
    if job["state"] != "completed" or job["mode"] not in ("android", "web"):
        raise LabError("source_requires_completed_static_job")
    if (not isinstance(filename, str) or not 1 <= len(filename) <= 220
            or filename.startswith("/") or "\\" in filename
            or not filename.lower().endswith((".java", ".kt", ".xml", ".json", ".js", ".html", ".txt"))):
        raise LabError("invalid_source_path")
    rel = PurePosixPath(filename)
    if any(part in (".", "..", "") for part in rel.parts):
        raise LabError("invalid_source_path")
    if type(first_line) is not int or not 1 <= first_line <= 500_000:
        raise LabError("invalid_line_number")
    base = (RESULTS / job_id / "sources").resolve()
    target = base.joinpath(*rel.parts)
    try:
        resolved = target.resolve(strict=True)
        if (not resolved.is_relative_to(base) or target.is_symlink()
                or not resolved.is_file() or resolved.stat().st_size > 2 * 1024 * 1024):
            raise LabError("invalid_source_file")
    except (OSError, ValueError) as exc:
        raise LabError("source_not_found") from exc
    lines = []
    with resolved.open(encoding="utf-8", errors="replace") as stream:
        for number, line in enumerate(stream, 1):
            if number < first_line:
                continue
            if number >= first_line + 35:
                break
            lines.append({"line": number, "text": line.rstrip("\r\n")[:300]})
    return {"ok": True, "job_id": job_id, "file": filename,
            "first_line": first_line, "lines": lines}


def list_jobs():
    """List persisted investigation IDs without exposing full artifacts."""
    jobs = []
    for folder in RESULTS.iterdir():
        if not folder.is_dir() or not JOB_ID.fullmatch(folder.name) or folder.is_symlink():
            continue
        try:
            data = read_job(folder.name)
            jobs.append({key: data.get(key) for key in
                        ("job_id", "artifact", "mode", "state", "started_epoch", "finished_epoch")})
        except (LabError, OSError, ValueError):
            continue
    jobs.sort(key=lambda item: item.get("started_epoch") or 0, reverse=True)
    return {"ok": True, "jobs": jobs[:100], "truncated": len(jobs) > 100}


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
                               "runtime_execution": False,
                               "source_index": True, "intelligence_packets": True,
                               "static_dependency_graph": True, "dashboard": "/dashboard",
                               "external_ai_execution": False,
                               "web_intelligence": True, "web_capture_service": "separate_public_https_sidecar",
                               "web_runtime_execution": False})
        elif self.command == "GET" and parts == ["jobs"]:
            self.respond(200, list_jobs())
        elif self.command == "POST" and parts == ["jobs", "web"]:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 4096:
                raise LabError("invalid_request_length")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict) or set(data) != {"url"}:
                raise LabError("invalid_fields")
            self.respond(202, start_web_job(data["url"]))
        elif self.command == "POST" and parts == ["jobs"]:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 4096:
                raise LabError("invalid_request_length")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict) or set(data) != {"filename", "mode"}:
                raise LabError("invalid_fields")
            self.respond(202, start_job(data["filename"], data["mode"]))
        elif self.command == "POST" and len(parts) == 3 and parts[0] == "jobs" and parts[2] == "index":
            self.respond(202, start_index(parts[1]))
        elif self.command == "GET" and len(parts) >= 2 and parts[0] == "jobs":
            job_id = parts[1]
            if len(parts) == 2:
                status = read_job(job_id)
                progress_file = RESULTS / job_id / "progress.json"
                if progress_file.is_file():
                    status["progress"] = json.loads(progress_file.read_text(encoding="utf-8"))
                self.respond(200, {"ok": True, **status})
            elif len(parts) == 3 and parts[2] == "summary":
                qs = parse_qs(parsed.query)
                if set(qs) != {"offset", "limit"}:
                    raise LabError("invalid_pagination")
                self.respond(200, compact_summary(job_id, int(qs["offset"][0]), int(qs["limit"][0])))
            elif len(parts) == 3 and parts[2] == "index":
                self.respond(200, index_state(job_id))
            elif len(parts) == 3 and parts[2] == "index-search":
                qs = parse_qs(parsed.query)
                if set(qs) != {"q"} or len(qs["q"]) != 1:
                    raise LabError("invalid_query")
                self.respond(200, intelligence().indexed_search(ready_index(job_id), qs["q"][0]))
            elif len(parts) == 3 and parts[2] == "graph":
                qs = parse_qs(parsed.query)
                if set(qs) != {"q"} or len(qs["q"]) != 1:
                    raise LabError("invalid_query")
                root = ready_index(job_id)
                if read_job(job_id)["mode"] == "web":
                    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
                    self.respond(200, web_intelligence().web_graph(report, qs["q"][0]))
                else:
                    self.respond(200, intelligence().dependency_graph(root, qs["q"][0]))
            elif len(parts) == 3 and parts[2] == "investigate":
                qs = parse_qs(parsed.query)
                if set(qs) != {"q", "focus"} or any(len(x) != 1 for x in qs.values()):
                    raise LabError("invalid_query")
                root = ready_index(job_id)
                packet = intelligence().investigate(root, qs["q"][0], qs["focus"][0])
                if read_job(job_id)["mode"] == "web":
                    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
                    packet["dependencies"] = web_intelligence().web_graph(report, qs["q"][0])["edges"]
                    packet["assessment"] = "Static browser-side references only; backend/model behavior unknown."
                self.respond(200, packet)
            elif len(parts) == 3 and parts[2] == "compare":
                qs = parse_qs(parsed.query)
                if set(qs) != {"other"} or len(qs["other"]) != 1:
                    raise LabError("invalid_web_compare")
                newer = qs["other"][0]
                if (read_job(job_id)["mode"] != "web" or read_job(newer)["mode"] != "web"
                        or read_job(job_id)["state"] != "completed" or read_job(newer)["state"] != "completed"):
                    raise LabError("web_jobs_required")
                old = json.loads((RESULTS / job_id / "report.json").read_text())
                new = json.loads((RESULTS / newer / "report.json").read_text())
                self.respond(200, web_intelligence().compare_web_reports(old, new))
            elif len(parts) == 3 and parts[2] == "report":
                if read_job(job_id)["state"] != "completed":
                    raise LabError("report_not_ready")
                self.respond(200, {"ok": True, **json.loads((RESULTS / job_id / "report.json").read_text())})
            elif len(parts) == 3 and parts[2] == "source":
                qs = parse_qs(parsed.query)
                if set(qs) != {"file", "line"} or any(len(values) != 1 for values in qs.values()):
                    raise LabError("invalid_source_request")
                self.respond(200, source_excerpt(job_id, qs["file"][0], int(qs["line"][0])))
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
        if urlsplit(self.path).path == "/dashboard":
            # Dashboard shell only. All evidence endpoints still require token auth.
            raw = Path(__file__).with_name("dashboard.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        else:
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
