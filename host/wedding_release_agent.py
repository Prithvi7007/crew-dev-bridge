"""Fixed-target, code-only Wedding V3 release agent (operator install only).

Host-installed, root-owned, and deliberately separate from the CREW release agent.
No user-controlled commands, paths, repository addresses, commits, SQL or flags.
New schema, dependency, environment and upload-layout changes require a separate
operator-reviewed deployment. NEVER restore an old DB over guest RSVPs.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
from pathlib import Path
import pwd
import re
import socket
import socketserver
import stat
import struct
import subprocess
import threading
import time
from urllib.request import Request, urlopen

ROOT = Path("/var/www/wedding-v3-101f73e")
# The live checkout is a DETACHED linked Git worktree. Its metadata lives
# outside ROOT; both paths must match exactly to prevent an arbitrary checkout.
GIT_DIRECTORY = Path("/var/www/wedding/.git/worktrees/wedding-v3-101f73e")
GIT_COMMON_DIRECTORY = Path("/var/www/wedding/.git")
UPLOADS = ROOT / "uploads"
SERVICE = "wedding.service"
APP_SOCKET = Path("/run/wedding/wedding.sock")
SERVICE_HOST = "adlinprithvi.cloud"
STATE = Path("/var/lib/wedding-release")
BACKUPS = Path("/var/backups/wedding")
AGENT_SOCKET = Path("/run/wedding-release/release.sock")
DB = "wedding_db"
SCRATCH_DB = "wedding_release_verify"
REPO = "https://github.com/Prithvi7007/Wedding_Website_Final.git"
API = "https://api.github.com/repos/Prithvi7007/Wedding_Website_Final"
BASE = "main"
CI_WORKFLOW = "Wedding V3 CI"
BRIDGE_UID = 10001
HEX = re.compile(r"[0-9a-f]{40}\Z")
OPS = frozenset({"capabilities", "plan", "start", "status"})
GATE_DIRS = ("app/models/", "migrations/", "scripts/", ".github/", "alembic/")
GATE_FILES = frozenset({"requirements.txt", "app/config.py", "app/extensions.py",
                        "wsgi.py", ".gitignore", "pyproject.toml"})
GUARD = threading.Lock()
_active = False


class Stop(Exception):
    """Expected fail-closed release outcome."""


def result(ok: bool, **kwargs):
    return {"ok": ok, **kwargs}


def run(argv, *, user=None, timeout=60, stdin=None, stdout=None):
    """Only server-owned constant argv. No shell or inherited secrets."""
    opts = {}
    if user:
        ident = pwd.getpwnam(user)
        opts = {"user": ident.pw_uid, "group": ident.pw_gid, "extra_groups": []}
    try:
        p = subprocess.run(
            list(argv), shell=False, check=False, stdin=stdin, stdout=stdout or subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=timeout, **opts,
            env={"PATH": "/usr/sbin:/usr/bin:/bin", "LANG": "C.UTF-8",
                 "HOME": "/tmp", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_CONFIG_GLOBAL": "/dev/null"},
        )
    except (OSError, subprocess.TimeoutExpired, KeyError) as e:
        raise Stop("command_unavailable") from e
    if p.returncode:
        raise Stop("command_failed")
    if stdout:
        return ""
    return p.stdout.decode("utf-8", "replace").strip()[:12000]


def git(*args, timeout=60):
    return run(["git", "-c", "core.hooksPath=/dev/null",
                "-c", "core.pager=cat", "-C", str(ROOT), *args], timeout=timeout)


def require_inactive_photo_uploads():
    """Block automatic releases as soon as photo storage becomes active.

    Photo upload functionality is not implemented. When it is introduced,
    a separately reviewed backup strategy must protect persisted media.
    """
    if UPLOADS.is_symlink() or UPLOADS.exists():
        raise Stop("photo_storage_requires_manual_review")
    count = postgres(["psql", "-d", DB, "-Atqc",
                      "SELECT COUNT(*) FROM public.photo_uploads"], timeout=20)
    if not count.isdecimal() or int(count) != 0:
        raise Stop("photo_upload_records_require_manual_review")


def verify_detached_worktree():
    """Require the observed production worktree, not a writable branch checkout.

    HEAD is intentionally detached. We obtain the approved target from GitHub's
    protected `main` instead of switching branches in the running checkout.
    """
    gitfile = ROOT / ".git"
    if (not ROOT.is_dir() or ROOT.is_symlink() or not gitfile.is_file()
            or gitfile.is_symlink()):
        raise Stop("invalid_checkout")
    try:
        marker = gitfile.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise Stop("invalid_checkout")
    if marker.strip() != f"gitdir: {GIT_DIRECTORY}":
        raise Stop("unexpected_worktree_metadata")
    if (not GIT_DIRECTORY.is_dir() or GIT_DIRECTORY.is_symlink()
            or not GIT_COMMON_DIRECTORY.is_dir() or GIT_COMMON_DIRECTORY.is_symlink()):
        raise Stop("unexpected_worktree_metadata")
    if (git("rev-parse", "--show-toplevel") != str(ROOT)
            or git("rev-parse", "--absolute-git-dir") != str(GIT_DIRECTORY)
            or git("rev-parse", "--git-common-dir") != str(GIT_COMMON_DIRECTORY)):
        raise Stop("unexpected_worktree_metadata")
    if git("rev-parse", "--abbrev-ref", "HEAD") != "HEAD":
        raise Stop("expected_detached_head")


def preflight():
    verify_detached_worktree()
    if git("remote", "get-url", "origin") != REPO:
        raise Stop("unexpected_remote")
    if git("status", "--porcelain"):
        raise Stop("dirty_checkout")
    old = git("rev-parse", "HEAD")
    if not HEX.fullmatch(old):
        raise Stop("invalid_revision")
    require_inactive_photo_uploads()
    if not APP_SOCKET.is_socket() or APP_SOCKET.is_symlink():
        raise Stop("app_socket_not_verified")
    if not BACKUPS.is_dir() or BACKUPS.is_symlink():
        raise Stop("backup_directory_not_ready")
    if not STATE.is_dir() or STATE.is_symlink():
        raise Stop("state_directory_not_ready")
    return old


def target():
    url = f"{API}/branches/{BASE}"
    req = Request(url, headers={"Accept": "application/vnd.github+json",
                                 "User-Agent": "Wedding-V3-release-agent"})
    try:
        with urlopen(req, timeout=8) as response:
            data = json.load(response)
        sha = data["commit"]["sha"]
    except (OSError, KeyError, ValueError, TypeError):
        raise Stop("github_branch_unavailable")
    if not isinstance(sha, str) or not HEX.fullmatch(sha):
        raise Stop("invalid_target")
    return sha


def ci_green(sha: str) -> bool:
    """Require the exact successful, completed GitHub Actions push run."""
    if not isinstance(sha, str) or not HEX.fullmatch(sha):
        return False
    url = f"{API}/actions/runs?branch={BASE}&head_sha={sha}&per_page=50"
    req = Request(url, headers={"Accept": "application/vnd.github+json",
                                 "User-Agent": "Wedding-V3-release-agent"})
    try:
        with urlopen(req, timeout=8) as response:
            payload = json.load(response)
        runs = payload.get("workflow_runs", [])
        return any(
            item.get("name") == CI_WORKFLOW
            and item.get("head_sha") == sha
            and item.get("head_branch") == BASE
            and item.get("event") == "push"
            and item.get("status") == "completed"
            and item.get("conclusion") == "success"
            for item in runs if isinstance(item, dict)
        )
    except (OSError, ValueError, TypeError):
        return False


def allowed_changes(old: str, new: str):
    paths = git("diff", "--name-only", "--no-renames", old, new).splitlines()
    for path in paths:
        if any(part.startswith(("photo", "upload", "gallery")) for part in path.lower().split("/")):
            raise Stop("photo_feature_changes_require_manual_review")
        if path in GATE_FILES or path.startswith(GATE_DIRS) or path.startswith(".env"):
            raise Stop("schema_or_runtime_change_requires_manual_review")
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", path) or ".." in path.split("/"):
            raise Stop("unsafe_change_path")
    # A symlink can affect runtime or permissions; only operator can approve it.
    summary = git("diff", "--summary", old, new)
    if "120000" in summary or "symlink" in summary.lower():
        raise Stop("symlink_change_requires_manual_review")
    return len(paths)


def health() -> bool:
    """Probe the fixed Gunicorn Unix socket; verify /readyz queries PostgreSQL."""
    class Connection(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(4)
            self.sock.connect(str(APP_SOCKET))
    try:
        conn = Connection("localhost", timeout=4)
        conn.request("GET", "/readyz", headers={"Host": SERVICE_HOST})
        resp = conn.getresponse()
        raw = resp.read(1024)
        conn.close()
        return resp.status == 200 and json.loads(raw).get("status") == "ready"
    except (OSError, ValueError, TypeError, http.client.HTTPException):
        return False


def status_state():
    file = STATE / "status.json"
    try:
        if file.is_symlink() or file.stat().st_size > 4096:
            return result(False, error="invalid_status")
        return json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return result(True, state="idle")


def save(state):
    STATE.mkdir(parents=True, exist_ok=True)
    file = STATE / "status.json"
    temp = STATE / "status.pending"
    raw = json.dumps(state, separators=(",", ":"), allow_nan=False)
    if len(raw) > 4096:
        raise Stop("state_too_large")
    temp.write_text(raw, encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, file)


def report_phase(job, phase, **extra):
    save(result(True, job=job, state="running", phase=phase, **extra))


def plan():
    try:
        old = preflight()
        sha = target()
        ci = ci_green(sha)
        return result(True, current_sha=old, candidate_sha=sha,
                      ci_green=ci, up_to_date=(old == sha),
                      code_only_policy="schema_changes_blocked",
                      executable=bool(ci and old != sha))
    except Stop as e:
        return result(False, error=str(e))


def postgres(argv, *, timeout=180, stdin=None, stdout=None):
    return run(argv, user="postgres", timeout=timeout, stdin=stdin, stdout=stdout)


def backup(job):
    # Root-opened file descriptors prevent PostgreSQL needing filesystem access
    # to the private backup dir; its subprocess never gets the bridge token.
    dump = BACKUPS / f"{job}.dump"
    manifest = BACKUPS / f"{job}.sha256"
    if dump.exists() or manifest.exists():
        raise Stop("backup_exists")
    try:
        with dump.open("xb") as fd:
            postgres(["pg_dump", "-Fc", "--dbname", DB], timeout=600, stdout=fd)
        os.chmod(dump, 0o600)
        sha = {}
        for item in (dump,):
            with item.open("rb") as stream:
                sha[item.name] = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest.write_text("\n".join(f"{value}  {key}" for key, value in sha.items()) + "\n")
        os.chmod(manifest, 0o600)
        return dump
    except OSError:
        raise Stop("backup_failed")


def rehearse(dump: Path):
    # Never touch an existing scratch DB. Never restore over wedding_db.
    exists = postgres(["psql", "-d", "postgres", "-Atqc",
                       f"SELECT 1 FROM pg_database WHERE datname = '{SCRATCH_DB}'"])
    if exists:
        raise Stop("scratch_database_exists")
    postgres(["createdb", SCRATCH_DB], timeout=30)
    try:
        with dump.open("rb") as fd:
            postgres(["pg_restore", "--no-owner", "--no-acl", "-d", SCRATCH_DB],
                     stdin=fd, timeout=600)
        value = postgres(["psql", "-d", SCRATCH_DB, "-Atqc",
                          "SELECT count(*) FROM public.invitations"], timeout=20)
        if not value.isdigit():
            raise Stop("scratch_restore_invalid")
    finally:
        postgres(["dropdb", "--if-exists", SCRATCH_DB], timeout=60)


def verify_readiness(attempts=12):
    for _ in range(attempts):
        if health():
            return True
        time.sleep(2)
    return False


def execute(job):
    previous = None
    changed = False
    try:
        report_phase(job, "preflight")
        previous = preflight()
        sha = target()
        if previous == sha:
            save(result(True, job=job, state="passed", phase="already_current",
                        deployed_sha=previous))
            return
        if not ci_green(sha):
            raise Stop("quality_gate_not_green")
        report_phase(job, "fetch")
        git("fetch", "--no-tags", "origin", f"refs/heads/{BASE}", timeout=90)
        fetched = git("rev-parse", "FETCH_HEAD")
        if fetched != sha:
            raise Stop("branch_advanced_since_ci")
        try:
            git("merge-base", "--is-ancestor", previous, sha)
        except Stop:
            raise Stop("non_fast_forward_release")
        count = allowed_changes(previous, sha)
        if count == 0:
            raise Stop("no_release_changes")
        if not health():
            raise Stop("production_not_ready")
        report_phase(job, "backup")
        dump = backup(job)
        report_phase(job, "scratch_restore")
        rehearse(dump)
        if not ci_green(sha) or target() != sha:
            raise Stop("quality_gate_changed")
        report_phase(job, "deploy")
        git("reset", "--hard", sha, timeout=90)
        changed = True
        run(["systemctl", "restart", SERVICE], timeout=40)
        report_phase(job, "verify")
        if not verify_readiness():
            raise Stop("new_version_not_ready")
        deployed = git("rev-parse", "HEAD")
        if deployed != sha:
            raise Stop("deployed_sha_mismatch")
        save(result(True, job=job, state="passed", phase="complete",
                    deployed_sha=sha, previous_sha=previous,
                    backup_id=job))
    except Exception as e:
        code = str(e) if isinstance(e, Stop) else "unexpected_release_error"
        if changed and previous and HEX.fullmatch(previous):
            try:
                report_phase(job, "rollback")
                git("reset", "--hard", previous)
                run(["systemctl", "restart", SERVICE], timeout=40)
                if not verify_readiness():
                    raise Stop("rollback_health_failed")
                save(result(False, job=job, state="failed", error=code,
                            rolled_back=True, backup_id=job))
                return
            except Exception:
                save(result(False, job=job, state="manual_recovery_required",
                            error="code_rollback_failed", backup_id=job))
                return
        save(result(False, job=job, state="failed", error=code,
                    production_code_changed=False))


def dispatch(request):
    global _active
    if not isinstance(request, dict) or set(request) != {"operation"}:
        return result(False, error="operation_not_allowed")
    op = request.get("operation")
    if op not in OPS:
        return result(False, error="operation_not_allowed")
    if op == "capabilities":
        return result(True, operations=sorted(OPS), target=BASE, service=SERVICE,
                      migration_policy="code_only", release_agent="wedding_v1")
    if op == "status":
        return status_state()
    if op == "plan":
        return plan()
    with GUARD:
        if _active:
            return result(False, error="release_already_running")
        state = status_state()
        if state.get("state") in {"running", "manual_recovery_required"}:
            return result(False, error="previous_release_requires_review")
        readiness = plan()
        if not readiness.get("executable"):
            return result(False, error=readiness.get("error") or "preflight_not_ready")
        _active = True
        job = time.strftime("%Y%m%d%H%M%S", time.gmtime())
        report_phase(job, "queued")
        def work():
            global _active
            try:
                execute(job)
            finally:
                with GUARD:
                    _active = False
        threading.Thread(target=work, daemon=True).start()
        return result(True, state="queued", job=job)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            self.request.settimeout(10)
            credentials = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _pid, uid, _gid = struct.unpack("3i", credentials)
            if uid != BRIDGE_UID:
                response = result(False, error="peer_not_authorized")
            else:
                raw = self.rfile.readline(1025)
                if len(raw) > 1024 or not raw.endswith(b"\n"):
                    response = result(False, error="invalid_request")
                else:
                    try:
                        response = dispatch(json.loads(raw))
                    except (ValueError, TypeError, UnicodeError):
                        response = result(False, error="invalid_request")
            self.wfile.write(json.dumps(response, separators=(",", ":")).encode()[:4096] + b"\n")
        except OSError:
            pass


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main():
    if os.geteuid() != 0:
        raise RuntimeError("Host release agent requires separately reviewed root installation")
    AGENT_SOCKET.parent.mkdir(parents=True, exist_ok=True)
    if AGENT_SOCKET.is_symlink():
        raise RuntimeError("Refusing symlinked socket")
    if AGENT_SOCKET.exists():
        if not stat.S_ISSOCK(AGENT_SOCKET.stat().st_mode):
            raise RuntimeError("Unexpected release socket path")
        AGENT_SOCKET.unlink()
    os.umask(0o077)
    with Server(str(AGENT_SOCKET), Handler) as srv:
        AGENT_SOCKET.chmod(0o660)
        srv.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
