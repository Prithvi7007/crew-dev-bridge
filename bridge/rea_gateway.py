"""Bounded client for the independent, privileged-boundary REA host relay.

No filesystem paths, URLs, arbitrary REA tools, or host shell commands are
accepted. The server validates operations independently with SO_PEERCRED.
"""
from __future__ import annotations

import json
import socket

SOCKET = "/run/crew-rea/rea.sock"
MAX_REQUEST = 100 * 1024
MAX_REPLY = 24_000
FIELDS = {
    "capabilities": (),
    "status": (),
    "inspect_artifact": ("filename",),
    "inspect_managed": ("filename",),
    "vault_list": (),
    "vault_info": ("filename",),
    "vault_entries": ("filename", "expected_sha256", "offset", "limit"),
    "vault_evidence": ("filename", "expected_sha256", "collection", "offset", "limit"),
    "vault_begin": ("filename", "size", "sha256"),
    "vault_chunk": ("upload_id", "offset", "data_b64"),
    "vault_upload_status": ("upload_id",),
    "vault_finish": ("upload_id",),
    "vault_cancel": ("upload_id",),
    "vault_delete": ("filename", "expected_sha256"),
}


def call(operation: str, filename: str | None = None, **fields) -> dict:
    if operation not in FIELDS:
        return {"ok": False, "error": "operation_not_allowed"}
    if filename is not None:
        fields["filename"] = filename
    if "filename" in fields and (
        not isinstance(fields["filename"], str) or
        not 1 <= len(fields["filename"]) <= 108
    ):
        return {"ok": False, "error": "invalid_filename"}
    if set(fields) != set(FIELDS[operation]):
        return {"ok": False, "error": "invalid_arguments"}
    payload = {"operation": operation, **fields}
    try:
        wire = (json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n").encode()
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid_arguments"}
    if len(wire) > MAX_REQUEST:
        return {"ok": False, "error": "request_too_large"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(180)
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
