"""Client-side broker to the independently installed root-owned CREW release agent.

No shell, URLs, commits, file paths or migration names are accepted from MCP.
The host must separately authenticate SO_PEERCRED and revalidate everything.
"""
import json
import socket

SOCKET = "/run/crew-release/release.sock"
OPS = frozenset(("capabilities", "plan", "start", "status"))


def call(operation: str) -> dict:
    if operation not in OPS:
        return {"ok": False, "error": "operation_not_allowed"}
    payload = (json.dumps({"operation": operation}) + "\n").encode()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(15)
            client.connect(SOCKET)
            client.sendall(payload)
            response = bytearray()
            while not response.endswith(b"\n"):
                chunk = client.recv(4096)
                if not chunk or len(response) + len(chunk) > 8192:
                    return {"ok": False, "error": "invalid_release_response"}
                response.extend(chunk)
        result = json.loads(response)
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            return {"ok": False, "error": "invalid_release_response"}
        return result
    except (OSError, ValueError, UnicodeError):
        return {"ok": False, "error": "release_agent_unavailable"}
