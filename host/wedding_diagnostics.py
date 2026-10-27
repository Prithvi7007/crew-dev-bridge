"""Fixed, aggregate-only PostgreSQL reporting service for the wedding database.

Install as an unprivileged host service, separately from CREW. Never provide
arbitrary SQL, paths, DSNs, secrets or guest-level records to the MCP client.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import socketserver
import stat
import struct
import threading

SOCKET = Path("/run/wedding-diagnostics/diag.sock")
BRIDGE_UID = 10001
MAX_MESSAGE = 512
MAX_RESPONSE = 4096
REPORTS = {
    "overview": ("SELECT invitation_count, event_count, rsvp_count, photo_upload_count, "
                 "change_request_count FROM wedding_reporting.overview"),
    "attendance": ("SELECT attending_yes, attending_no, attending_maybe, "
                   "confirmed_guest_count FROM wedding_reporting.attendance"),
    "health": "SELECT status FROM wedding_reporting.health",
}
BUSY = threading.BoundedSemaphore(4)


def error(code: str) -> dict:
    return {"ok": False, "error": code}


def dispatch(message) -> dict:
    if not isinstance(message, dict) or set(message) != {"operation", "report"}:
        return error("operation_not_allowed")
    if message["operation"] != "report" or type(message["report"]) is not str:
        return error("operation_not_allowed")
    report = message["report"]
    if report not in REPORTS:
        return error("report_not_allowed")
    return execute(report)


def execute(report: str) -> dict:
    if report not in REPORTS:
        return error("report_not_allowed")
    credentials = os.environ.get("CREDENTIALS_DIRECTORY", "")
    dsn_file = Path(credentials) / "report_dsn" if credentials else None
    if not dsn_file or not dsn_file.is_file():
        return error("reporting_not_configured")
    try:
        import psycopg

        dsn = dsn_file.read_text(encoding="utf-8").strip()
        if not dsn:
            return error("reporting_not_configured")
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SET LOCAL statement_timeout = '3000ms'")
            conn.execute("SET LOCAL lock_timeout = '500ms'")
            with conn.cursor() as cur:
                cur.execute(REPORTS[report])
                names = [item.name for item in cur.description or []]
                rows = cur.fetchmany(2)
        if len(rows) != 1 or len(names) > 8:
            return error("invalid_report_result")
        values = rows[0]
        # All values must be aggregate integers or the fixed health status.
        if report == "health":
            if names != ["status"] or values[0] != "ready":
                return error("health_not_ready")
        elif any(type(v) is not int or v < 0 for v in values):
            return error("invalid_report_result")
        result = {"ok": True, "report": report, "data": dict(zip(names, values))}
        if len(json.dumps(result).encode("utf-8")) > MAX_RESPONSE - 1:
            return error("report_too_large")
        return result
    except Exception:
        # Never return SQL, DSNs, row data, errors or connection details.
        return error("reporting_unavailable")


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        if not BUSY.acquire(blocking=False):
            self.wfile.write(b'{"ok":false,"error":"busy"}\n')
            return
        try:
            self.request.settimeout(8)
            creds = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _pid, uid, _gid = struct.unpack("3i", creds)
            if uid != BRIDGE_UID:
                result = error("peer_not_authorized")
            else:
                raw = self.rfile.readline(MAX_MESSAGE + 1)
                if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
                    result = error("invalid_request")
                else:
                    try:
                        result = dispatch(json.loads(raw))
                    except (ValueError, TypeError, UnicodeError):
                        result = error("invalid_request")
            output = json.dumps(result, separators=(",", ":"), allow_nan=False).encode()
            self.wfile.write((output if len(output) < MAX_RESPONSE else
                             b'{"ok":false,"error":"response_too_large"}') + b"\n")
        except OSError:
            pass
        finally:
            BUSY.release()


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 8


def main():
    if os.geteuid() == 0:
        raise RuntimeError("Wedding reporting worker must not run as root")
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET.is_symlink():
        raise RuntimeError("Refusing symlink socket")
    if SOCKET.exists():
        if not stat.S_ISSOCK(SOCKET.stat().st_mode):
            raise RuntimeError("Unexpected socket path")
        SOCKET.unlink()
    os.umask(0o007)
    with Server(str(SOCKET), Handler) as server:
        SOCKET.chmod(0o660)
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
