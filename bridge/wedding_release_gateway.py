"""Fixed-operation broker for the separately installed Wedding V3 release service.

No arbitrary commands, SQL, paths, commit IDs or migration switches travel over MCP.
"""
import json
import socket

SOCKET = "/run/wedding-release/release.sock"
OPS = frozenset({"capabilities", "plan", "start", "status"})


def call(operation: str) -> dict:
    if operation not in OPS:
        return {"ok": False, "error": "operation_not_allowed"}
    message = (json.dumps({"operation": operation}) + "\n").encode()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(15)
            client.connect(SOCKET)
            client.sendall(message)
            response = bytearray()
            while not response.endswith(b"\n"):
                chunk = client.recv(2048)
                if not chunk or len(chunk) + len(response) > 8192:
                    return {"ok": False, "error": "invalid_release_response"}
                response.extend(chunk)
        value = json.loads(response)
        if type(value) is not dict or type(value.get("ok")) is not bool:
            return {"ok": False, "error": "invalid_release_response"}
        return value
    except (OSError, ValueError, TypeError, UnicodeError):
        return {"ok": False, "error": "wedding_release_agent_unavailable"}
