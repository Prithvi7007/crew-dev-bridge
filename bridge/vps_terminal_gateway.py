"""Private Unix-socket client for the opt-in, root-capable VPS terminal.

The host worker is the only process with host execution capabilities. The
bridge has no Docker socket or host filesystem mount. This API is powerful:
the assistant must seek explicit user approval before calling execute.
"""
from __future__ import annotations

import json
import re
import socket

SOCKET = "/run/crew-vps-terminal/terminal.sock"
MAX_REPLY = 20_000
MAX_REQUEST = 12_000
JOB_ID = re.compile(r"[a-f0-9]{32}\Z")
DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def _call(payload):
    wire = (json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n").encode()
    if len(wire) > MAX_REQUEST:
        return {"ok": False, "error": "request_too_large"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(12)
            client.connect(SOCKET)
            client.sendall(wire)
            result = bytearray()
            while not result.endswith(b"\n"):
                part = client.recv(4096)
                if not part or len(result) + len(part) > MAX_REPLY:
                    return {"ok": False, "error": "invalid_terminal_response"}
                result.extend(part)
        decoded = json.loads(result)
        if not isinstance(decoded, dict) or not isinstance(decoded.get("ok"), bool):
            return {"ok": False, "error": "invalid_terminal_response"}
        return decoded
    except (OSError, ValueError, TypeError, UnicodeError):
        return {"ok": False, "error": "vps_terminal_unavailable"}


def capabilities():
    return _call({"operation": "capabilities"})


def prepare(command: str, timeout_seconds: int = 300, suppress_output: bool = False):
    """Plan arbitrary shell command but DO NOT run it."""
    if (not isinstance(command, str) or not command.strip()
            or len(command.encode("utf-8")) > 8192 or "\x00" in command
            or type(timeout_seconds) is not int or not 5 <= timeout_seconds <= 1800
            or type(suppress_output) is not bool):
        return {"ok": False, "error": "invalid_command_or_limits"}
    return _call({"operation": "prepare", "command": command,
                  "timeout_seconds": timeout_seconds, "suppress_output": suppress_output})


def execute(ticket: str, command_sha256: str):
    """Start a previously prepared command after explicit human chat approval."""
    if (not isinstance(ticket, str) or not JOB_ID.fullmatch(ticket)
            or not isinstance(command_sha256, str) or not DIGEST.fullmatch(command_sha256)):
        return {"ok": False, "error": "invalid_ticket_or_digest"}
    return _call({"operation": "execute", "ticket": ticket, "command_sha256": command_sha256})


def status(job_id: str):
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        return {"ok": False, "error": "invalid_job_id"}
    return _call({"operation": "status", "job_id": job_id})
