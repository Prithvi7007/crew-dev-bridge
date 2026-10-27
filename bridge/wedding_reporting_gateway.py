"""Bridge client for fixed, aggregate-only Wedding V3 PostgreSQL diagnostics.

No caller-supplied SQL, database names, paths, or credentials. The host service
independently enforces the report allowlist and Unix peer authorization.
"""
from __future__ import annotations

import json
import socket

SOCKET = "/run/wedding-diagnostics/diag.sock"
REPORTS = frozenset({"overview", "attendance", "health"})
MAX_REPLY = 4096


def call(report: str = "overview") -> dict:
    if not isinstance(report, str) or report not in REPORTS:
        return {"ok": False, "error": "report_not_allowed"}
    wire = (json.dumps({"operation": "report", "report": report}) + "\n").encode()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(8)
            conn.connect(SOCKET)
            conn.sendall(wire)
            response = bytearray()
            while not response.endswith(b"\n"):
                part = conn.recv(2048)
                if not part or len(response) + len(part) > MAX_REPLY:
                    return {"ok": False, "error": "invalid_report_response"}
                response.extend(part)
        result = json.loads(response)
        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            return {"ok": False, "error": "invalid_report_response"}
        return result
    except (OSError, ValueError, TypeError, UnicodeError):
        return {"ok": False, "error": "wedding_diagnostics_unavailable"}
