"""Bounded gateway to the independently installed REA Lite host relay.

No Docker socket, network destination, arbitrary tool name, or host paths
are supplied by the caller. The host relay independently authenticates the
bridge UID and validates every requested operation.
"""
from __future__ import annotations

import json
import socket

SOCKET = "/run/crew-rea/rea.sock"
VALID_OPERATIONS = frozenset(("capabilities", "status", "inspect_artifact"))
MAX_REQUEST = 1024
MAX_REPLY = 24_000


def call(operation: str, filename: str | None = None) -> dict:
    if operation not in VALID_OPERATIONS:
        return {"ok": False, "error": "operation_not_allowed"}
    payload = {"operation": operation}
    if operation == "inspect_artifact":
        if not isinstance(filename, str) or not 1 <= len(filename) <= 108:
            return {"ok": False, "error": "invalid_filename"}
        payload["filename"] = filename
    wire = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
    if len(wire) > MAX_REQUEST:
        return {"ok": False, "error": "request_too_large"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(105)
            conn.connect(SOCKET)
            conn.sendall(wire)
            buf = bytearray()
            while not buf.endswith(b"\n"):
                chunk = conn.recv(4096)
                if not chunk or len(buf) + len(chunk) > MAX_REPLY:
                    return {"ok": False, "error": "invalid_rea_relay_reply"}
                buf.extend(chunk)
        data = json.loads(buf)
        if not isinstance(data, dict) or not isinstance(data.get("ok"), bool):
            return {"ok": False, "error": "invalid_rea_relay_reply"}
        return data
    except (OSError, ValueError, UnicodeError):
        return {"ok": False, "error": "rea_relay_unavailable"}


def capabilities() -> dict:
    return call("capabilities")
