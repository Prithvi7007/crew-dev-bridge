"""Private, audited VPS shell executor for CREW MCP.

This is an intentionally powerful *opt-in root executor*. One private Unix
socket, exact-command two-step preview/execute, bounded jobs and output.
Never expose the socket over TCP or to untrusted container identities.

Security note: command confirmation in a chat is a workflow rule, not a
cryptographic proof of human approval. Any compromised bridge process that
can access this socket can execute root commands. Install only knowingly.
"""
from __future__ import annotations

from collections import deque
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import signal
import socket
import socketserver
import stat
import struct
import subprocess
import threading
import time

SOCKET = Path("/run/crew-vps-terminal/terminal.sock")
STATE_DIR = Path("/var/lib/crew-vps-terminal")
BRIDGE_UID = 10001
MAX_REQUEST = 12_000
MAX_RESPONSE = 20_000
MAX_COMMAND_BYTES = 8192
MAX_OUTPUT_BYTES = 6000  # Each of stdout/stderr; never send unbounded logs through MCP.
VALID_JOB = re.compile(r"[a-f0-9]{32}\Z")
TIMEOUT_MIN = 5
TIMEOUT_MAX = 1800
PLAN_TTL = 600
PLAN_LIMIT = 24
PROCESSES = 1
ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/root",
    "LANG": "C.UTF-8",
    "GIT_TERMINAL_PROMPT": "0",
}
_LOCK = threading.RLock()
_PENDING: dict[str, dict] = {}
_JOBS: dict[str, dict] = {}
_ACTIVE: str | None = None
_RUNNING: dict[str, subprocess.Popen] = {}


def _error(reason: str) -> dict:
    return {"ok": False, "error": reason}


def _valid_command(command, timeout_seconds, suppress_output):
    if not isinstance(command, str) or not command.strip():
        return False
    if len(command.encode("utf-8")) > MAX_COMMAND_BYTES or "\x00" in command:
        return False
    if type(timeout_seconds) is not int or not TIMEOUT_MIN <= timeout_seconds <= TIMEOUT_MAX:
        return False
    return type(suppress_output) is bool


def _cleanup_pending():
    current = time.monotonic()
    for token in list(_PENDING):
        if _PENDING[token]["expires"] <= current:
            _PENDING.pop(token, None)


def prepare(command: str, timeout_seconds: int = 300, suppress_output: bool = False) -> dict:
    """Prepare arbitrary VPS shell command for explicit chat-level approval."""
    if not _valid_command(command, timeout_seconds, suppress_output):
        return _error("invalid_command_or_limits")
    with _LOCK:
        _cleanup_pending()
        if len(_PENDING) >= PLAN_LIMIT:
            return _error("too_many_pending_commands")
        ticket = secrets.token_hex(16)
        digest = hashlib.sha256(command.encode("utf-8")).hexdigest()
        _PENDING[ticket] = {
            "command": command, "timeout": timeout_seconds, "digest": digest,
            "suppress_output": suppress_output, "expires": time.monotonic() + PLAN_TTL,
        }
    return {"ok": True, "ticket": ticket, "command_sha256": digest,
            "command_bytes": len(command.encode("utf-8")), "timeout_seconds": timeout_seconds,
            "suppress_output": suppress_output, "effective_identity": "root",
            "expires_in_seconds": PLAN_TTL, "requires_explicit_chat_approval": True,
            "warning": "Execution is unrestricted shell under root. Approval is a chat workflow, not enforced cryptographically."}


def _write_status(job):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / (job["job_id"] + ".json")
    tmp = STATE_DIR / (job["job_id"] + ".tmp")
    data = {k: v for k, v in job.items() if k != "command"}
    raw = json.dumps(data, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    with tmp.open("w", encoding="utf-8") as fp:
        os.fchmod(fp.fileno(), 0o600)
        fp.write(raw)
    tmp.replace(path)


def _capture(pipe, received, limit):
    with pipe:
        while True:
            chunk = pipe.read(4096)
            if not chunk:
                break
            received["total"] += len(chunk)
            received["data"].extend(chunk)
            if len(received["data"]) > limit:
                del received["data"][:-limit]


def _execute_job(job_id, script, timeout_seconds, suppress_output):
    global _ACTIVE
    started = int(time.time())
    with _LOCK:
        job = _JOBS[job_id]
        job.update({"state": "running", "started_epoch": started})
        _write_status(job)

    process = None
    out = {"data": bytearray(), "total": 0}
    err = {"data": bytearray(), "total": 0}
    timed_out = False
    returncode = None
    problem = None
    try:
        process = subprocess.Popen(
            ["/bin/bash", "-lc", script],
            cwd="/", env=ENV, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True, close_fds=True,
        )
        with _LOCK:
            _RUNNING[job_id] = process
        t1 = threading.Thread(target=_capture, args=(process.stdout, out, MAX_OUTPUT_BYTES), daemon=True)
        t2 = threading.Thread(target=_capture, args=(process.stderr, err, MAX_OUTPUT_BYTES), daemon=True)
        t1.start()
        t2.start()
        try:
            returncode = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            returncode = process.wait(timeout=10)
        t1.join(timeout=2)
        t2.join(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        problem = "host_execution_failed"
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    finally:
        with _LOCK:
            job = _JOBS[job_id]
            job.update({
                "state": "timed_out" if timed_out else "failed" if problem or returncode != 0 else "completed",
                "exit_code": returncode, "error": problem,
                "finished_epoch": int(time.time()),
                "stdout_tail": "" if suppress_output else out["data"].decode("utf-8", errors="replace"),
                "stderr_tail": "" if suppress_output else err["data"].decode("utf-8", errors="replace"),
                "stdout_truncated": out["total"] > MAX_OUTPUT_BYTES,
                "stderr_truncated": err["total"] > MAX_OUTPUT_BYTES,
                "output_suppressed": suppress_output,
            })
            _write_status(job)
            _RUNNING.pop(job_id, None)
            if _ACTIVE == job_id:
                _ACTIVE = None


def execute(ticket: str, command_sha256: str) -> dict:
    """Consume the exact prepared command; no command text supplied at execute."""
    global _ACTIVE
    if not isinstance(ticket, str) or not VALID_JOB.fullmatch(ticket):
        return _error("invalid_ticket")
    if not isinstance(command_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", command_sha256):
        return _error("invalid_command_digest")
    with _LOCK:
        _cleanup_pending()
        proposal = _PENDING.get(ticket)
        if not proposal or proposal["digest"] != command_sha256:
            return _error("unknown_or_mismatched_command")
        if _ACTIVE is not None:
            return _error("vps_command_busy")
        job_id = secrets.token_hex(16)
        _PENDING.pop(ticket)
        _ACTIVE = job_id
        _JOBS[job_id] = {
            "job_id": job_id, "state": "queued",
            "effective_identity": "root",
            "command_sha256": proposal["digest"],
            "timeout_seconds": proposal["timeout"],
            "output_suppressed": proposal["suppress_output"],
            "submitted_epoch": int(time.time()),
        }
        _write_status(_JOBS[job_id])
        thread = threading.Thread(
            target=_execute_job,
            args=(job_id, proposal["command"], proposal["timeout"], proposal["suppress_output"]),
            daemon=True,
        )
        thread.start()
    return {"ok": True, "job_id": job_id, "state": "queued",
            "command_sha256": command_sha256}


def status(job_id: str) -> dict:
    if not isinstance(job_id, str) or not VALID_JOB.fullmatch(job_id):
        return _error("invalid_job_id")
    with _LOCK:
        if job_id in _JOBS:
            return {"ok": True, **_JOBS[job_id]}
    path = STATE_DIR / (job_id + ".json")
    if not path.is_file() or path.is_symlink():
        return _error("job_not_found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["state"] in ("queued", "running"):
            data["state"] = "interrupted"
        return {"ok": True, **data}
    except (OSError, ValueError, KeyError):
        return _error("job_state_unavailable")


def dispatch(request):
    if not isinstance(request, dict):
        return _error("invalid_request")
    operation = request.get("operation")
    if operation == "capabilities" and set(request) == {"operation"}:
        return {"ok": True, "operations": ["prepare", "execute", "status"],
                "effective_identity": "root", "shell": "/bin/bash",
                "max_command_bytes": MAX_COMMAND_BYTES,
                "timeout_range_seconds": [TIMEOUT_MIN, TIMEOUT_MAX],
                "max_parallel_jobs": PROCESSES,
                "requires_explicit_chat_approval": True}
    if operation == "prepare" and set(request) == {"operation", "command", "timeout_seconds", "suppress_output"}:
        return prepare(request["command"], request["timeout_seconds"], request["suppress_output"])
    if operation == "execute" and set(request) == {"operation", "ticket", "command_sha256"}:
        return execute(request["ticket"], request["command_sha256"])
    if operation == "status" and set(request) == {"operation", "job_id"}:
        return status(request["job_id"])
    return _error("operation_not_allowed")


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(8)
        try:
            credentials = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _pid, uid, _gid = struct.unpack("3i", credentials)
            if uid != BRIDGE_UID:
                response = _error("peer_not_authorized")
            else:
                raw = self.rfile.readline(MAX_REQUEST + 1)
                if not raw.endswith(b"\n") or len(raw) > MAX_REQUEST:
                    response = _error("invalid_request_size")
                else:
                    try:
                        response = dispatch(json.loads(raw))
                    except (ValueError, TypeError, UnicodeError):
                        response = _error("invalid_request")
            payload = json.dumps(response, ensure_ascii=True, separators=(",", ":")).encode()
            if len(payload) > MAX_RESPONSE:
                payload = b'{"ok":false,"error":"response_too_large"}'
            self.wfile.write(payload + b"\n")
            # Audit the operation metadata; never log raw commands or output.
            print(json.dumps({"event": "crew_vps_terminal",
                              "result_ok": bool(response.get("ok")),
                              "operation": response.get("state", "request")}),
                  flush=True)
        except (OSError, ValueError):
            return


class Gateway(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main():
    if os.geteuid() != 0:
        raise RuntimeError("VPS terminal host service requires explicit root install")
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if SOCKET.is_symlink():
        raise RuntimeError("refusing_symlinked_socket")
    if SOCKET.exists():
        item = SOCKET.lstat()
        if not stat.S_ISSOCK(item.st_mode) or item.st_uid != 0:
            raise RuntimeError("unexpected_existing_socket")
        with socket.socket(socket.AF_UNIX) as probe:
            probe.settimeout(1)
            try:
                probe.connect(str(SOCKET))
            except ConnectionRefusedError:
                SOCKET.unlink()
            else:
                raise RuntimeError("terminal_socket_already_active")
    os.umask(0o077)
    with Gateway(str(SOCKET), Handler) as server:
        os.chmod(SOCKET, 0o660)
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
