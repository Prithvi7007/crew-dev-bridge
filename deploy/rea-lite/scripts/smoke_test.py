#!/usr/bin/env python3
"""REA Lite MCP protocol smoke test, using only Python stdlib.

No files are uploaded and no target application is executed. The same session
is used for initialize, notifications/initialized, tools/list, binary_session,
and DELETE.
"""
import argparse
import json
import sys
import time
from urllib import error, request

ENDPOINT = "http://127.0.0.1:18765/mcp"
PROTOCOL = "2025-06-18"


def message(method, request_id=None, params=None):
    value = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        value["id"] = request_id
    if params is not None:
        value["params"] = params
    return json.dumps(value, separators=(",", ":")).encode()


def decode(body, content_type):
    content = body.decode("utf-8")
    if "text/event-stream" in content_type:
        frames = []
        for frame in content.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(line[5:].lstrip() for line in frame.splitlines() if line.startswith("data:"))
            if data:
                frames.append(json.loads(data))
        if not frames:
            raise ValueError("SSE had no result frame")
        return frames[-1]
    return json.loads(content)


def send(payload=None, session=None, method="POST"):
    headers = {"Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": PROTOCOL}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if session:
        headers["Mcp-Session-Id"] = session
    req = request.Request(ENDPOINT, data=payload, headers=headers, method=method)
    with request.urlopen(req, timeout=10) as response:
        body = response.read(2 * 1024 * 1024)
        sid = response.headers.get("Mcp-Session-Id")
        if method == "DELETE" or (payload is not None and b'"id"' not in payload):
            return None, sid
        return decode(body, response.headers.get("Content-Type", "")), sid


def verify():
    session = None
    try:
        init, session = send(message("initialize", 1, {
            "protocolVersion": PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "rea-lite-smoke", "version": "1.0.0"},
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
            except (error.URLError, ValueError) as exc:
                print(f"Warning: MCP session DELETE failed: {type(exc).__name__}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--retries", type=int, default=1)
    args = parser.parse_args()
    for index in range(max(1, args.retries)):
        try:
            verify()
            return 0
        except (error.URLError, TimeoutError, ValueError, KeyError) as exc:
            if index + 1 >= args.retries:
                print(f"FAIL: MCP smoke test: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 1
            time.sleep(2)
    return 1


if __name__ == "__main__":
    sys.exit(main())
