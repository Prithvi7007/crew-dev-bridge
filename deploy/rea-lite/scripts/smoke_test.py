#!/usr/bin/env python3
"""REA Lite MCP smoke test, using only Python standard library.

Read complete bounded JSON responses and complete SSE event frames. The REA
initialize response contains extensive server instructions (>2 MiB), so cutting
the response at a fixed 2 MiB produces an invalid-JSON false alarm.

No artifacts are uploaded or executed. One session is used for initialize,
notifications/initialized, tools/list, binary_session, and DELETE.
"""
import argparse
import json
import sys
import time
from urllib import request

ENDPOINT = "http://127.0.0.1:18765/mcp"
PROTOCOL = "2025-06-18"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


def message(method, request_id=None, params=None):
    value = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        value["id"] = request_id
    if params is not None:
        value["params"] = params
    return json.dumps(value, separators=(",", ":")).encode()


def decode(body, content_type):
    """Decode a complete buffered reply (also used by offline unit tests)."""
    content = body.decode("utf-8")
    if "text/event-stream" in content_type:
        frames = []
        for frame in content.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(
                line[5:].lstrip() for line in frame.splitlines()
                if line.startswith("data:")
            )
            if data:
                frames.append(json.loads(data))
        if not frames:
            raise ValueError("SSE had no result frame")
        return frames[-1]
    return json.loads(content)


def read_response(response, expected_id):
    """Read a bounded full JSON reply or the complete matching SSE message."""
    content_type = response.headers.get("Content-Type", "")
    if "text/event-stream" not in content_type:
        size = response.headers.get("Content-Length")
        if size is not None and int(size) > MAX_RESPONSE_BYTES:
            raise ValueError("MCP response exceeds 32 MiB limit (Content-Length)")
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("MCP response exceeds 32 MiB limit")
        return decode(body, content_type)

    received = 0
    data_lines = []
    while True:
        remaining = MAX_RESPONSE_BYTES + 1 - received
        if remaining <= 0:
            raise ValueError("MCP SSE response exceeds 32 MiB limit")
        line = response.readline(remaining)
        if not line:
            raise ValueError("MCP SSE ended before the matching JSON-RPC response")
        received += len(line)
        if received > MAX_RESPONSE_BYTES:
            raise ValueError("MCP SSE response exceeds 32 MiB limit")
        stripped = line.decode("utf-8").rstrip("\r\n")
        if not stripped:
            if data_lines:
                payload = json.loads("\n".join(data_lines))
                if payload.get("id") == expected_id:
                    return payload
                data_lines.clear()
        elif stripped.startswith("data:"):
            data_lines.append(stripped[5:].lstrip())


def send(payload=None, session=None, method="POST"):
    headers = {"Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": PROTOCOL}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if session:
        headers["Mcp-Session-Id"] = session
    req = request.Request(ENDPOINT, data=payload, headers=headers, method=method)
    with request.urlopen(req, timeout=30) as response:
        sid = response.headers.get("Mcp-Session-Id")
        # Notifications receive 202, and DELETE receives 200 with no response
        # body. Do not try to read/parse them as JSON or SSE.
        if method == "DELETE" or (payload is not None and b'"id"' not in payload):
            return None, sid
        expected_id = json.loads(payload)["id"]
        return read_response(response, expected_id), sid


def verify():
    session = None
    try:
        init, session = send(message("initialize", 1, {
            "protocolVersion": PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "rea-lite-smoke", "version": "1.0.1"},
        }))
        if init.get("id") != 1 or "result" not in init or not session:
            raise ValueError("MCP initialize failed")
        send(message("notifications/initialized"), session)
        catalog, _ = send(message("tools/list", 2), session)
        if catalog.get("id") != 2 or "result" not in catalog:
            raise ValueError("MCP tools/list failed")
        names = {tool["name"] for tool in catalog["result"].get("tools", [])}
        if "binary_session" not in names:
            raise ValueError("binary_session missing; inspect tool family configuration")
        result, _ = send(message("tools/call", 3, {
            "name": "binary_session", "arguments": {},
        }), session)
        if result.get("id") != 3 or "result" not in result or result["result"].get("isError"):
            raise ValueError("MCP binary_session call failed")
        print(f"PASS: MCP handshake, {len(names)} tools advertised, binary_session healthy")
    finally:
        if session:
            try:
                send(session=session, method="DELETE")
            except (OSError, ValueError) as exc:
                print(f"Warning: MCP session DELETE failed: {type(exc).__name__}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--retries", type=int, default=1)
    args = parser.parse_args()
    for index in range(max(1, args.retries)):
        try:
            verify()
            return 0
        except (OSError, ValueError, KeyError) as exc:
            if index + 1 >= args.retries:
                print(f"FAIL: MCP smoke test: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 1
            time.sleep(2)
    return 1


if __name__ == "__main__":
    sys.exit(main())
