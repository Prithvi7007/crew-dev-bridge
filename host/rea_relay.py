"""Unprivileged REA Lite relay for the existing CREW MCP tunnel.

Only three fixed operations: capabilities, status, inspect_artifact.
The relay is a separate host trust boundary; the MCP bridge cannot pass raw
REA tool names, arbitrary arguments, shell commands, paths, or URLs.
REA target contents are untrusted data, not instructions.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import socketserver
import stat
import struct
from urllib import request

SOCKET = Path("/run/crew-rea/rea.sock")
ARTIFACTS = Path("/opt/rea-lite/artifacts")
REA_URL = "http://127.0.0.1:18765/mcp"
BRIDGE_UID = 10001
PROTOCOL = "2025-06-18"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_RESULT_BYTES = 12_000
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
ALLOWED_EXTENSIONS = (".apk", ".aab", ".zip", ".jar", ".asar")
NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
ALLOWED_OPERATIONS = frozenset(("capabilities", "status", "inspect_artifact"))


class RelayError(Exception):
    pass


def validate_artifact(filename: str) -> dict:
    """Allow only ordinary, named files directly inside the operator upload dir."""
    if not isinstance(filename, str) or not NAME_PATTERN.fullmatch(filename):
        raise RelayError("invalid_filename")
    if filename in (".", "..") or filename.startswith(".") or not filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise RelayError("unsupported_artifact_name")
    if ARTIFACTS.is_symlink() or not ARTIFACTS.is_dir():
        raise RelayError("artifact_directory_unavailable")
    path = ARTIFACTS / filename
    try:
        item = path.lstat()
    except OSError as exc:
        raise RelayError("artifact_not_found") from exc
    if not stat.S_ISREG(item.st_mode) or path.is_symlink() or item.st_nlink != 1:
        raise RelayError("invalid_artifact_type")
    if not 0 < item.st_size <= MAX_ARTIFACT_BYTES:
        raise RelayError("invalid_artifact_size")
    # REA runs in a separate unprivileged container; the host relay never reads
    # the file bytes and never accepts a caller-selected absolute path.
    return {"filename": filename, "bytes": item.st_size,
            "container_path": "/work/artifacts/" + filename}


def _jsonrpc(method: str, request_id: int | None = None, params: dict | None = None) -> bytes:
    payload = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        payload["id"] = request_id
    if params is not None:
        payload["params"] = params
    return json.dumps(payload, separators=(",", ":")).encode()


def _read_reply(response, expected_id: int) -> dict:
    kind = response.headers.get("Content-Type", "")
    if "text/event-stream" not in kind:
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_RESPONSE_BYTES:
            raise RelayError("mcp_response_too_large")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise RelayError("mcp_response_too_large")
        decoded = json.loads(raw)
    else:
        total = 0
        segments = []
        while True:
            line = response.readline(MAX_RESPONSE_BYTES + 1 - total)
            if not line:
                raise RelayError("mcp_sse_ended_without_reply")
            total += len(line)
            if total > MAX_RESPONSE_BYTES:
                raise RelayError("mcp_response_too_large")
            part = line.decode("utf-8").rstrip("\r\n")
            if part.startswith("data:"):
                segments.append(part[5:].lstrip())
            if not part and segments:
                decoded = json.loads("\n".join(segments))
                segments.clear()
                if decoded.get("id") == expected_id:
                    break
    if not isinstance(decoded, dict) or decoded.get("id") != expected_id:
        raise RelayError("invalid_mcp_reply")
    return decoded


def mcp_request(method: str, request_id: int | None = None,
                params: dict | None = None, session: str | None = None,
                http_method: str = "POST") -> tuple[dict | None, str | None]:
    wire = _jsonrpc(method, request_id, params) if http_method == "POST" else None
    headers = {"Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": PROTOCOL}
    if http_method == "POST":
        headers["Content-Type"] = "application/json"
    if session:
        headers["Mcp-Session-Id"] = session
    req = request.Request(REA_URL, data=wire, headers=headers, method=http_method)
    with request.urlopen(req, timeout=65) as response:
        session_id = response.headers.get("Mcp-Session-Id")
        if request_id is None or http_method == "DELETE":
            return None, session_id
        return _read_reply(response, request_id), session_id


def _tool(name: str, arguments: dict, request_id: int, session: str) -> dict:
    # Names are hardcoded by the host; never accept caller-provided tool names.
    if name not in ("binary_session", "open_binary", "inspect_artifact", "close_binary"):
        raise RelayError("tool_not_allowed")
    raw, _ = mcp_request("tools/call", request_id,
                         {"name": name, "arguments": arguments}, session)
    result = raw.get("result") if raw else None
    if not isinstance(result, dict) or result.get("isError"):
        raise RelayError("rea_" + name + "_failed")
    return result


def _summary(result: dict) -> dict:
    """Return only a bounded structured summary, no raw target bytes or logs."""
    structured = result.get("structuredContent")
    if structured is None:
        structured = {"content": result.get("content", [])}
    if not isinstance(structured, (dict, list)):
        raise RelayError("invalid_rea_output")
    # Truncate the serialized payload with explicit disclosure, preserving
    # evidence identity where available. It is NOT a complete forensic record.
    envelope = json.dumps(structured, ensure_ascii=True, separators=(",", ":"))
    limit = MAX_RESULT_BYTES
    fields = {"untrusted_target_data": True,
              "result_excerpt": envelope[:limit],
              "truncated": len(envelope) > limit}
    if isinstance(structured, dict):
        eid = structured.get("evidence_id")
        if isinstance(eid, str) and len(eid) <= 160:
            fields["evidence_id"] = eid
    return fields


def analyze(operation: str, filename: str | None = None) -> dict:
    if operation not in ("status", "inspect_artifact"):
        return {"ok": False, "error": "operation_not_allowed"}
    artifact = None
    if operation == "inspect_artifact":
        artifact = validate_artifact(filename)
    session = None
    try:
        initialized, session = mcp_request(
            "initialize", 1,
            {"protocolVersion": PROTOCOL, "capabilities": {},
             "clientInfo": {"name": "crew-rea-relay", "version": "1.0"}}
        )
        if not session or initialized.get("id") != 1 or "result" not in initialized:
            raise RelayError("rea_mcp_initialize_failed")
        mcp_request("notifications/initialized", session=session)
        if operation == "status":
            result = _tool("binary_session", {}, 2, session)
            return {"ok": True, "operation": "status", **_summary(result)}
        _tool("open_binary", {"path": artifact["container_path"]}, 2, session)
        result = _tool("inspect_artifact", {"integrity_policy": "fail",
                                             "detail": "summary"}, 3, session)
        return {"ok": True, "operation": "inspect_artifact",
                "filename": artifact["filename"], "bytes": artifact["bytes"],
                **_summary(result)}
    finally:
        if session:
            if operation == "inspect_artifact":
                try:
                    _tool("close_binary", {}, 4, session)
                except (RelayError, OSError, ValueError):
                    pass
            try:
                mcp_request("", session=session, http_method="DELETE")
            except (OSError, RelayError, ValueError):
                pass


def handle(payload: object) -> dict:
    if not isinstance(payload, dict) or set(payload) - {"operation", "filename"}:
        return {"ok": False, "error": "invalid_request"}
    operation = payload.get("operation")
    if operation not in ALLOWED_OPERATIONS:
        return {"ok": False, "error": "operation_not_allowed"}
    if operation == "capabilities":
        return {"ok": True, "operations": ["capabilities", "status", "inspect_artifact"],
                "transport": "existing_crew_tunnel_via_unix_socket",
                "requires_manual_operator_artifact_upload": True,
                "executable_or_dynamic_tools_available": False}
    if operation == "status" and "filename" in payload:
        return {"ok": False, "error": "invalid_request"}
    if operation == "inspect_artifact" and "filename" not in payload:
        return {"ok": False, "error": "filename_required"}
    try:
        return analyze(operation, payload.get("filename"))
    except RelayError as exc:
        return {"ok": False, "error": str(exc)}
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
        return {"ok": False, "error": "rea_mcp_unavailable_or_invalid"}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        # Authenticate peer at the HOST boundary, not via MCP arguments.
        creds = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        _, uid, _ = struct.unpack("3i", creds)
        if uid != BRIDGE_UID:
            return
        self.request.settimeout(120)
        raw = self.rfile.readline(2049)
        if not raw.endswith(b"\n") or len(raw) > 2048:
            result = {"ok": False, "error": "invalid_request_size"}
        else:
            try:
                result = handle(json.loads(raw))
            except (ValueError, UnicodeError):
                result = {"ok": False, "error": "invalid_json"}
        reply = (json.dumps(result, separators=(",", ":")) + "\n").encode()
        if len(reply) > 24_000:
            reply = b'{"ok":false,"error":"rea_reply_too_large"}\n'
        self.wfile.write(reply)


def main():
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    # On a systemd restart an unclean exit can leave a stale Unix-socket
    # filesystem entry. Remove ONLY a socket owned by this fixed worker UID,
    # and only after proving that no process accepts a connection to it.
    if SOCKET.exists() or SOCKET.is_symlink():
        item = SOCKET.lstat()
        if not stat.S_ISSOCK(item.st_mode) or item.st_uid != os.geteuid():
            raise SystemExit("Unexpected or unowned REA socket; operator review required")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            try:
                probe.connect(str(SOCKET))
            except ConnectionRefusedError:
                SOCKET.unlink()
            else:
                raise SystemExit("REA relay socket is already serving")
    os.umask(0o007)
    with socketserver.UnixStreamServer(str(SOCKET), Handler) as server:
        os.chmod(SOCKET, 0o660)
        server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
