"""Unprivileged UNIX-socket host diagnostics gateway for CREW.

No shell, no arbitrary host file reads, and no production DB credentials in the MCP
container. The gateway must run with an exclusively read-only reporting DB role.
Never expose this Unix socket over TCP.
"""
from __future__ import annotations

import decimal
import json
import os
from pathlib import Path
import re
import socket
import socketserver
import stat
import struct
import subprocess
import threading
import time
from typing import Any

SOCKET_PATH = Path(os.environ.get("CREW_HOST_DIAG_SOCKET", "/run/crew-host-diag/host.sock"))
BRIDGE_UID = int(os.environ.get("CREW_HOST_DIAG_BRIDGE_UID", "10001"))
MAX_REQUEST = 8192
MAX_RESPONSE = 32768
COMMANDS = frozenset({"uptime", "disk", "memory", "service", "git_revision"})
SERVICES = frozenset({
    "crew.service", "nginx.service", "docker.service",
    "crew-deploy.service", "crew-host-diag.service",
})
GIT_ROOTS = {
    "crew_games": "/opt/crew",
    "crew-dev-bridge": "/opt/crew-dev-bridge",
}
DISK_PATHS = frozenset({"/", "/opt/crew", "/opt/crew-dev-bridge"})
CAPACITY = threading.BoundedSemaphore(value=4)


def _error(code: str) -> dict:
    return {"ok": False, "error": code}


def command_argv(command: str, args: list[str]) -> list[str]:
    """Map a narrow diagnostic request to fixed argv. Never invoke a shell."""
    if command not in COMMANDS or not isinstance(args, list):
        raise ValueError("command_not_allowed")
    if any(not isinstance(x, str) or len(x) > 80 for x in args):
        raise ValueError("invalid_arguments")
    if command == "uptime" and not args:
        return ["uptime", "-p"]
    if command == "memory" and not args:
        return ["free", "-m"]
    if command == "disk" and (not args or (len(args) == 1 and args[0] in DISK_PATHS)):
        return ["df", "-hP", args[0] if args else "/"]
    if command == "service" and len(args) == 1 and args[0] in SERVICES:
        return ["systemctl", "show", "--no-pager",
                "--property=LoadState,ActiveState,SubState,MainPID", args[0]]
    if command == "git_revision" and len(args) == 1 and args[0] in GIT_ROOTS:
        return ["git", "-C", GIT_ROOTS[args[0]], "rev-parse", "HEAD"]
    raise ValueError("invalid_arguments")


def execute_command(command: str, args: list[str]) -> dict:
    try:
        argv = command_argv(command, args)
    except ValueError as exc:
        return _error(str(exc))
    try:
        proc = subprocess.run(
            argv, shell=False, capture_output=True, text=True,
            timeout=5, check=False,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                 "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                 "GIT_TERMINAL_PROMPT": "0"},
        )
        return {"ok": proc.returncode == 0, "command": command,
                "exit_code": proc.returncode, "stdout": proc.stdout[:8000]}
    except (OSError, subprocess.TimeoutExpired):
        return _error("command_unavailable")


def validate_sql(sql: Any, params: Any, limit: Any) -> bool:
    """Narrow syntax gate. PostgreSQL DB permissions are the real boundary."""
    if not isinstance(sql, str) or not 1 <= len(sql) <= 4000:
        return False
    if not re.match(r"^\s*SELECT\b", sql, flags=re.IGNORECASE):
        return False
    if any(x in sql for x in (";", "--", "/*", "*/", "\x00", "$$")):
        return False
    if re.search(r"\bINTO\b|\bFOR\s+(UPDATE|SHARE)\b", sql, flags=re.IGNORECASE):
        return False
    if not isinstance(params, list) or len(params) > 16:
        return False
    for p in params:
        if p is None or isinstance(p, (bool, int)):
            continue
        if isinstance(p, float) and p == p and abs(p) != float("inf"):
            continue
        if isinstance(p, str) and len(p) <= 256:
            continue
        return False
    return isinstance(limit, int) and not isinstance(limit, bool) and 1 <= limit <= 100


def _value(data: Any) -> Any:
    if data is None or isinstance(data, (bool, int, float)):
        return data
    if isinstance(data, str):
        return data[:256]
    if isinstance(data, (bytes, bytearray, memoryview)):
        return "[binary_redacted]"
    if isinstance(data, decimal.Decimal):
        return str(data)[:80]
    return str(data)[:256]


def execute_db(sql: str, params: list, limit: int) -> dict:
    if not validate_sql(sql, params, limit):
        return _error("query_not_allowed")
    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY", "")
    cred_file = Path(cred_dir) / "db_dsn" if cred_dir else None
    if cred_file is None or not cred_file.is_file():
        return _error("reporting_database_not_configured")
    try:
        # Imported here so host gateway syntax/command tests require no DB packages.
        import psycopg
        dsn = cred_file.read_text(encoding="utf-8").strip()
        if not dsn:
            return _error("reporting_database_not_configured")
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SET LOCAL statement_timeout = '4000ms'")
            conn.execute("SET LOCAL lock_timeout = '1000ms'")
            conn.execute("SET LOCAL search_path = crew_reporting, pg_catalog")
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if cur.description is None or len(cur.description) > 20:
                    return _error("result_not_allowed")
                cols = [x.name[:64] for x in cur.description]
                data = cur.fetchmany(limit + 1)
                truncated = len(data) > limit
                rows = [dict(zip(cols, (_value(x) for x in row)))
                        for row in data[:limit]]
        response = {"ok": True, "columns": cols, "rows": rows,
                    "truncated": truncated, "row_count": len(rows)}
        # Never return a huge response through MCP or the Unix gateway.
        if len(json.dumps(response, allow_nan=False).encode("utf-8")) > 22000:
            return _error("result_too_large_reduce_limit")
        return response
    except Exception:
        # No SQL, parameter values, DSN, database errors or stack traces leave host.
        return _error("reporting_query_failed")


def dispatch(request: Any) -> dict:
    if not isinstance(request, dict):
        return _error("invalid_request")
    op = request.get("operation")
    if op == "vps_exec" and set(request) == {"operation", "command", "args"}:
        return execute_command(request["command"], request["args"])
    if op == "db_query" and set(request) == {"operation", "sql", "params", "limit"}:
        return execute_db(request["sql"], request["params"], request["limit"])
    return _error("operation_not_allowed")


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(10)
        started = time.monotonic()
        acquired = CAPACITY.acquire(blocking=False)
        if not acquired:
            self.wfile.write(b'{"ok":false,"error":"gateway_busy"}\n')
            return
        operation = "invalid"
        ok = False
        try:
            # Authenticate the connecting Linux UID, not a user-provided field.
            cred = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _pid, uid, _gid = struct.unpack("3i", cred)
            if uid != BRIDGE_UID:
                result = _error("peer_not_authorized")
            else:
                raw = self.rfile.readline(MAX_REQUEST + 1)
                if not raw.endswith(b"\n") or len(raw) > MAX_REQUEST:
                    result = _error("request_too_large")
                else:
                    try:
                        req = json.loads(raw)
                        operation = req.get("operation", "invalid") if isinstance(req, dict) else "invalid"
                        result = dispatch(req)
                    except (ValueError, TypeError, UnicodeError):
                        result = _error("invalid_request")
            encoded = json.dumps(result, separators=(",", ":"), allow_nan=False).encode("utf-8")
            if len(encoded) > MAX_RESPONSE - 1:
                encoded = b'{"ok":false,"error":"response_too_large"}'
            self.wfile.write(encoded + b"\n")
            ok = bool(result.get("ok"))
        except (OSError, ValueError):
            pass
        finally:
            print(json.dumps({"event": "crew_host_diagnostics",
                              "operation": operation if operation in {"db_query", "vps_exec"} else "invalid",
                              "ok": ok, "duration_ms": int((time.monotonic() - started) * 1000)}),
                  flush=True)
            CAPACITY.release()


class Gateway(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 8


def main() -> None:
    if os.geteuid() == 0:
        raise RuntimeError("Host diagnostics must not run as root")
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.is_symlink():
        raise RuntimeError("Refusing symlinked diagnostic socket")
    if SOCKET_PATH.exists():
        if not stat.S_ISSOCK(SOCKET_PATH.stat().st_mode):
            raise RuntimeError("Refusing non-socket diagnostics path")
        SOCKET_PATH.unlink()
    os.umask(0o007)
    with Gateway(str(SOCKET_PATH), Handler) as server:
        SOCKET_PATH.chmod(0o660)
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
