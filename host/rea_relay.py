"""Unprivileged REA Lite relay for the existing CREW MCP tunnel.

Fixed operations cover status, static inspection and the dedicated vault.
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

try:
    from . import rea_store
except ImportError:  # installed as a standalone host script
    import rea_store

SOCKET = Path("/run/crew-rea/rea.sock")
ARTIFACTS = Path("/opt/rea-lite/artifacts")
REA_URL = "http://127.0.0.1:18765/mcp"
BRIDGE_UID = 10001
PROTOCOL = "2025-06-18"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_RESULT_BYTES = 12_000
MAX_DETAIL_BYTES = 12_000
MAX_VIEW_ROWS = 25
MAX_VIEW_OFFSET = 1_000_000
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
ALLOWED_EXTENSIONS = (".apk", ".aab", ".zip", ".jar", ".asar")
NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
EVIDENCE_ID = re.compile(r"ev_[0-9a-f]{64}\Z")
VIEW_OPERATIONS = ("vault_entries", "vault_evidence")
DETAIL_COLLECTIONS = ("nodes", "edges", "integrity_contradictions")
ENTRY_FIELDS = (
    "logical_path", "entry_kind", "artifact_format", "declared_size",
    "compressed_size", "encrypted", "executable", "hash_status",
)
NODE_FIELDS = (
    "format", "sha256", "size", "architecture", "content_state",
)
ALLOWED_OPERATIONS = frozenset((
    "capabilities", "status", "inspect_artifact", "inspect_managed",
    "vault_list", "vault_info", "vault_begin", "vault_chunk",
    "vault_upload_status", "vault_finish", "vault_cancel", "vault_delete",
    *VIEW_OPERATIONS,
))


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
    if name not in ("binary_session", "open_binary", "inspect_artifact",
                    "inspect_analysis_view", "close_binary"):
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


def _view_arguments(expected_sha256, collection, offset, limit):
    """Validate the complete caller boundary before hashing or opening REA."""
    rea_store.valid_sha(expected_sha256)
    if (type(offset) is not int or not 0 <= offset <= MAX_VIEW_OFFSET
            or type(limit) is not int or not 1 <= limit <= MAX_VIEW_ROWS):
        raise RelayError("invalid_page")
    if collection not in ("occurrences", *DETAIL_COLLECTIONS):
        raise RelayError("invalid_collection")


def _checked_evidence(result: dict, artifact: dict, kind: str) -> dict:
    """Admit only the expected view receipt tied to this exact vault artifact."""
    value = result.get("structuredContent")
    if not isinstance(value, dict):
        raise RelayError("invalid_rea_evidence")
    normalized = value.get("normalized_result")
    subject = value.get("subject")
    if (not isinstance(normalized, dict) or not isinstance(subject, dict)
            or normalized.get("kind") != kind
            or normalized.get("parent_operation") != "inspect_artifact"
            or value.get("operation") != "inspect_analysis_view"
            or not isinstance(normalized.get("artifact"), dict)
            or not isinstance(subject.get("digest"), dict)
            or not isinstance(value.get("evidence_id"), str)
            or not EVIDENCE_ID.fullmatch(value["evidence_id"])):
        raise RelayError("invalid_rea_evidence")
    observed = normalized["artifact"]
    if (subject["digest"].get("sha256") != artifact["sha256"]
            or subject.get("local_path") != artifact["container_path"]
            or observed.get("sha256") != artifact["sha256"]
            or observed.get("path") != artifact["container_path"]):
        raise RelayError("rea_evidence_mismatch")
    parent = normalized.get("parent_evidence_id")
    if not isinstance(parent, str) or not EVIDENCE_ID.fullmatch(parent):
        raise RelayError("invalid_rea_evidence")
    return value


def _inventory_page(operation, artifact, summary, collection, offset, limit, session):
    """Project retained inventory in this same session; never slice JSON text."""
    receipt = _checked_evidence(summary, artifact, "summary")
    parent = receipt["normalized_result"]["parent_evidence_id"]
    effective = limit
    request_id = 4
    while True:
        view = {"kind": "inventory", "collection": collection,
                "offset": offset, "limit": effective}
        if collection == "occurrences":
            view["fields"] = list(ENTRY_FIELDS)
        elif collection == "nodes":
            view["fields"] = list(NODE_FIELDS)
        result = _tool("inspect_analysis_view", {
            "source": {"kind": "retained-evidence", "evidence_id": parent},
            "view": view,
        }, request_id, session)
        value = _checked_evidence(result, artifact, "inventory")
        page = value["normalized_result"]
        items, coverage = page.get("items"), page.get("coverage")
        if (page.get("parent_evidence_id") != parent or page.get("view") != view
                or not isinstance(items, list) or len(items) > effective
                or type(page.get("source_complete")) is not bool
                or not isinstance(coverage, dict)):
            raise RelayError("invalid_rea_evidence")
        total = coverage.get("total")
        exhausted = type(total) is int and (offset >= total or offset + len(items) >= total)
        if (type(total) is not int or total < 0
                or type(coverage.get("examined")) is not int
                or coverage["examined"] != len(items)
                or coverage.get("exhausted") is not exhausted
                or coverage.get("next_offset") != (None if exhausted else offset + len(items))
                or coverage.get("status") != ("page" if items else "empty")):
            raise RelayError("invalid_rea_evidence")
        manifest = page.get("manifest")
        if not isinstance(manifest, dict) or manifest.get("root_sha256") != artifact["sha256"]:
            raise RelayError("rea_evidence_mismatch")
        envelope = {
            "ok": True, "operation": operation,
            "filename": artifact["filename"], "bytes": artifact["bytes"],
            "sha256": artifact["sha256"], "collection": collection,
            "offset": offset, "requested_limit": limit, "effective_limit": effective,
            "source_reinspected": True, "untrusted_target_data": True,
            "truncated": False, "evidence": value,
        }
        wire = json.dumps(envelope, ensure_ascii=True, separators=(",", ":"),
                          allow_nan=False).encode()
        if len(wire) <= MAX_DETAIL_BYTES:
            return envelope
        if effective == 1:
            raise RelayError("rea_page_too_large")
        effective = max(1, effective // 2)
        request_id += 1


def analyze(operation: str, filename: str | None = None, *,
            expected_sha256=None, collection=None, offset=0, limit=10) -> dict:
    if operation not in ("status", "inspect_artifact", "inspect_managed", *VIEW_OPERATIONS):
        return {"ok": False, "error": "operation_not_allowed"}
    artifact = None
    if operation in VIEW_OPERATIONS:
        collection = "occurrences" if operation == "vault_entries" else collection
        # Occurrences have their own fixed entry tool, not an arbitrary selector.
        if operation == "vault_evidence" and collection not in DETAIL_COLLECTIONS:
            raise RelayError("invalid_collection")
        _view_arguments(expected_sha256, collection, offset, limit)
        artifact = rea_store.file_info(filename)
        if artifact["sha256"] != expected_sha256:
            raise RelayError("sha256_mismatch")
    elif operation == "inspect_artifact":
        artifact = validate_artifact(filename)
    elif operation == "inspect_managed":
        artifact = rea_store.file_info(filename)
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
        if operation in VIEW_OPERATIONS:
            return _inventory_page(operation, artifact, result, collection, offset, limit, session)
        return {"ok": True, "operation": "inspect_artifact",
                "filename": artifact["filename"], "bytes": artifact["bytes"],
                **_summary(result)}
    finally:
        if session:
            if operation != "status":
                try:
                    _tool("close_binary", {}, 100 if operation in VIEW_OPERATIONS else 4, session)
                except (RelayError, OSError, ValueError):
                    pass
            try:
                mcp_request("", session=session, http_method="DELETE")
            except (OSError, RelayError, ValueError):
                pass


def handle(payload: object) -> dict:
    if not isinstance(payload, dict):
        return {"ok": False, "error": "invalid_request"}
    operation = payload.get("operation")
    allowed_fields = {
        "capabilities": {"operation"},
        "status": {"operation"},
        "inspect_artifact": {"operation", "filename"},
        "inspect_managed": {"operation", "filename"},
        "vault_list": {"operation"},
        "vault_info": {"operation", "filename"},
        "vault_entries": {"operation", "filename", "expected_sha256", "offset", "limit"},
        "vault_evidence": {"operation", "filename", "expected_sha256", "collection",
                           "offset", "limit"},
        "vault_begin": {"operation", "filename", "size", "sha256"},
        "vault_chunk": {"operation", "upload_id", "offset", "data_b64"},
        "vault_upload_status": {"operation", "upload_id"},
        "vault_finish": {"operation", "upload_id"},
        "vault_cancel": {"operation", "upload_id"},
        "vault_delete": {"operation", "filename", "expected_sha256"},
    }
    if operation not in allowed_fields:
        return {"ok": False, "error": "operation_not_allowed"}
    if operation in ("inspect_artifact", "inspect_managed") and "filename" not in payload:
        return {"ok": False, "error": "filename_required"}
    if set(payload) != allowed_fields[operation]:
        return {"ok": False, "error": "invalid_request"}
    if operation == "capabilities":
        return {"ok": True,
                "operations": sorted(ALLOWED_OPERATIONS),
                "transport": "existing_crew_tunnel_via_unix_socket",
                "vault_path": str(rea_store.ROOT),
                "max_file_bytes": rea_store.MAX_FILE_BYTES,
                "max_chunk_bytes": rea_store.MAX_CHUNK_BYTES,
                "max_view_rows": MAX_VIEW_ROWS,
                "max_view_offset": MAX_VIEW_OFFSET,
                "max_view_bytes": MAX_DETAIL_BYTES,
                "detail_collections": list(DETAIL_COLLECTIONS),
                "executable_or_dynamic_tools_available": False}
    try:
        if operation in VIEW_OPERATIONS:
            return analyze(operation, payload["filename"],
                           expected_sha256=payload["expected_sha256"],
                           collection=payload.get("collection"),
                           offset=payload["offset"], limit=payload["limit"])
        if operation in ("status", "inspect_artifact", "inspect_managed"):
            return analyze(operation, payload.get("filename"))
        if operation == "vault_list":
            return rea_store.list_files()
        if operation == "vault_info":
            return rea_store.file_info(payload["filename"])
        if operation == "vault_begin":
            return rea_store.begin_upload(payload["filename"], payload["size"],
                                          payload["sha256"])
        if operation == "vault_chunk":
            return rea_store.append_chunk(payload["upload_id"], payload["offset"],
                                          payload["data_b64"])
        if operation == "vault_upload_status":
            return rea_store.upload_status(payload["upload_id"])
        if operation == "vault_finish":
            return rea_store.finish_upload(payload["upload_id"])
        if operation == "vault_cancel":
            return rea_store.cancel_upload(payload["upload_id"])
        if operation == "vault_delete":
            return rea_store.delete_file(payload["filename"], payload["expected_sha256"])
    except (RelayError, rea_store.StoreError) as exc:
        return {"ok": False, "error": str(exc)}
    except (OSError, TimeoutError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return {"ok": False, "error": "rea_vault_or_mcp_error"}
    return {"ok": False, "error": "operation_not_allowed"}

class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        # Authenticate peer at the HOST boundary, not via MCP arguments.
        creds = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        _, uid, _ = struct.unpack("3i", creds)
        if uid != BRIDGE_UID:
            return
        self.request.settimeout(180)
        raw = self.rfile.readline(100 * 1024 + 1)
        if not raw.endswith(b"\n") or len(raw) > 100 * 1024:
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
