"""Bounded client for the optional, unprivileged CREW host diagnostics service.

The bridge has no host shell, direct PostgreSQL connection or credentials.
The host independently validates every request; this module is not a trust boundary.
"""
from __future__ import annotations

import json
import socket
from typing import Any

SOCKET = "/run/crew-host-diag/host.sock"
MAX_REQUEST = 8192
MAX_REPLY = 32768
VALID_COMMANDS = frozenset({"uptime", "disk", "memory", "service", "git_revision"})


def _call(payload: dict[str, Any]) -> dict:
    wire = (json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    if len(wire) > MAX_REQUEST:
        return {"ok": False, "error": "request_too_large"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(12)
            conn.connect(SOCKET)
            conn.sendall(wire)
            reply = bytearray()
            while not reply.endswith(b"\n"):
                chunk = conn.recv(4096)
                if not chunk or len(reply) + len(chunk) > MAX_REPLY:
                    return {"ok": False, "error": "invalid_gateway_response"}
                reply.extend(chunk)
        result = json.loads(reply)
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            return {"ok": False, "error": "invalid_gateway_response"}
        return result
    except (OSError, ValueError, TypeError, UnicodeError):
        return {"ok": False, "error": "host_gateway_unavailable"}


def db_query(sql: str, params: list | None = None, limit: int = 50) -> dict:
    """Read-only reporting SELECT on a separately permissioned DB role."""
    if not isinstance(sql, str) or not 1 <= len(sql) <= 4000:
        return {"ok": False, "error": "invalid_query"}
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        return {"ok": False, "error": "invalid_limit"}
    if params is None:
        params = []
    if not isinstance(params, list) or len(params) > 16:
        return {"ok": False, "error": "invalid_parameters"}
    for value in params:
        if value is None or isinstance(value, (bool, int)):
            continue
        if isinstance(value, float) and value == value and abs(value) != float("inf"):
            continue
        if isinstance(value, str) and len(value) <= 256:
            continue
        return {"ok": False, "error": "invalid_parameters"}
    return _call({"operation": "db_query", "sql": sql, "params": params, "limit": limit})


def vps_exec(command: str, args: list[str] | None = None) -> dict:
    """Execute one of the independently allowlisted host diagnostic operations."""
    if command not in VALID_COMMANDS:
        return {"ok": False, "error": "command_not_allowed", "allowed": sorted(VALID_COMMANDS)}
    if args is None:
        args = []
    if not isinstance(args, list) or len(args) > 3 or any(
        not isinstance(v, str) or not 1 <= len(v) <= 80 for v in args
    ):
        return {"ok": False, "error": "invalid_arguments"}
    return _call({"operation": "vps_exec", "command": command, "args": args})
