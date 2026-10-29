"""Security and protocol tests for scoped REA integration on the CREW tunnel.

All tests are offline. No Docker, REA server, files outside pytest tmpdir, or
production sockets are touched.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from bridge import rea_gateway
from host import rea_relay


def test_gateway_rejects_arbitrary_operations_and_bad_names():
    assert rea_gateway.call("shell")["error"] == "operation_not_allowed"
    assert rea_gateway.call("inspect_artifact", "")["error"] == "invalid_filename"
    assert rea_gateway.call("inspect_artifact", "x" * 110)["error"] == "invalid_filename"


def test_host_allowlist_and_no_arbitrary_paths():
    assert rea_relay.handle({"operation": "shell", "filename": "x.apk"})["error"] == "operation_not_allowed"
    assert rea_relay.handle({"operation": "capabilities", "command": "x"})["error"] == "invalid_request"
    assert rea_relay.handle({"operation": "inspect_artifact"})["error"] == "filename_required"
    assert rea_relay.handle({"operation": "status", "filename": "x.apk"})["error"] == "invalid_request"
    caps = rea_relay.handle({"operation": "capabilities"})
    assert caps["ok"]
    assert caps["executable_or_dynamic_tools_available"] is False
    assert set(caps["operations"]) == {"capabilities", "status", "inspect_artifact"}


@pytest.fixture
def staged(tmp_path, monkeypatch):
    monkeypatch.setattr(rea_relay, "ARTIFACTS", tmp_path)
    good = tmp_path / "example.apk"
    good.write_bytes(b"PK\x03\x04test")
    return tmp_path


def test_staged_artifact_path_is_file_only(staged):
    good = rea_relay.validate_artifact("example.apk")
    assert good["container_path"] == "/work/artifacts/example.apk"
    assert good["bytes"] == 8
    for name in ("../file.apk", "/etc/passwd", "sub/x.apk", "..", ".secret.apk",
                 "file.sh", "bad name.apk", "\\etc\\passwd.apk", "a" * 105 + ".apk"):
        with pytest.raises(rea_relay.RelayError):
            rea_relay.validate_artifact(name)


def test_staged_artifact_rejects_links_nested_paths_and_large_files(staged):
    (staged / "alias.zip").symlink_to(staged / "example.apk")
    with pytest.raises(rea_relay.RelayError):
        rea_relay.validate_artifact("alias.zip")
    os.link(staged / "example.apk", staged / "hardlink.jar")
    with pytest.raises(rea_relay.RelayError):
        rea_relay.validate_artifact("hardlink.jar")
    (staged / "empty.zip").touch()
    with pytest.raises(rea_relay.RelayError):
        rea_relay.validate_artifact("empty.zip")
    oversized = staged / "large.aab"
    with oversized.open("wb") as out:
        out.truncate(rea_relay.MAX_ARTIFACT_BYTES + 1)
    with pytest.raises(rea_relay.RelayError):
        rea_relay.validate_artifact("large.aab")


def test_no_user_selected_rea_tool_names():
    with pytest.raises(rea_relay.RelayError, match="tool_not_allowed"):
        rea_relay._tool("capture_process_scenario", {}, 1, "s")


def test_rea_status_lifecycle_uses_single_session(monkeypatch):
    events = []

    def fake_mcp(method, request_id=None, params=None, session=None, http_method="POST"):
        events.append((method, http_method, session))
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": 1, "result": {}}, "test-session"
        return None, None

    def fake_tool(name, args, request_id, session):
        events.append(("tool:" + name, "POST", session))
        return {"structuredContent": {"ready": True}}

    monkeypatch.setattr(rea_relay, "mcp_request", fake_mcp)
    monkeypatch.setattr(rea_relay, "_tool", fake_tool)
    result = rea_relay.handle({"operation": "status"})
    assert result["ok"]
    assert result["untrusted_target_data"] is True
    assert [name for name, _, _ in events] == [
        "initialize", "notifications/initialized", "tool:binary_session", "",
    ]
    assert events[-1][1] == "DELETE"


def test_rea_inspect_has_fixed_calls_and_closes_target(staged, monkeypatch):
    events = []

    def fake_mcp(method, request_id=None, params=None, session=None, http_method="POST"):
        events.append(("mcp", method, http_method))
        if method == "initialize":
            return {"id": 1, "result": {}}, "test-session"
        return None, None

    def fake_tool(name, args, request_id, session):
        events.append(("tool", name, args))
        if name == "open_binary":
            assert args == {"path": "/work/artifacts/example.apk"}
        if name == "inspect_artifact":
            assert args == {"integrity_policy": "fail", "detail": "summary"}
            return {"structuredContent": {"evidence_id": "ev_test", "normalized_result": {"status": "ok"}}}
        return {"structuredContent": {}}

    monkeypatch.setattr(rea_relay, "mcp_request", fake_mcp)
    monkeypatch.setattr(rea_relay, "_tool", fake_tool)
    result = rea_relay.handle({"operation": "inspect_artifact", "filename": "example.apk"})
    assert result["ok"] and result["evidence_id"] == "ev_test"
    assert [event[1] for event in events if event[0] == "tool"] == [
        "open_binary", "inspect_artifact", "close_binary",
    ]
    assert events[-1] == ("mcp", "", "DELETE")


def test_bounded_untrusted_evidence_return():
    output = rea_relay._summary({
        "structuredContent": {"evidence_id": "ev_example", "data": "X" * 20000}
    })
    assert output["truncated"] is True
    assert output["evidence_id"] == "ev_example"
    assert output["untrusted_target_data"] is True
    assert len(output["result_excerpt"]) == rea_relay.MAX_RESULT_BYTES


def test_single_mcp_reply_stream_parsing():
    body = io.BytesIO(b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n')
    body.headers = {"Content-Type": "text/event-stream"}
    assert rea_relay._read_reply(body, 1)["result"]["ok"] is True
    body = io.BytesIO(b'{"jsonrpc":"2.0","id":2,"result":{}}')
    body.headers = {"Content-Type": "application/json"}
    assert rea_relay._read_reply(body, 2)["id"] == 2


def test_socket_credential_validation_and_no_rea_access_from_container():
    source = Path(rea_relay.__file__).read_text()
    compose = (Path(__file__).parents[1] / "compose.yaml").read_text()
    unit = (Path(__file__).parents[1] / "deploy/rea-bridge/crew-rea-relay.service").read_text()
    installer = (Path(__file__).parents[1] / "deploy/rea-bridge/install.sh").read_text()
    assert "socket.SO_PEERCRED" in source
    assert "uid != BRIDGE_UID" in source
    assert "with socketserver.UnixStreamServer" in source
    assert "/run/crew-rea:/run/crew-rea:ro" in compose
    assert "docker.sock" not in compose
    assert "User=nobody" in unit
    assert "NoNewPrivileges=true" in unit
    assert "IPAddressDeny=any" in unit
    assert "IPAddressAllow=127.0.0.0/8" in unit
    assert "docker compose up -d --build --force-recreate --no-deps bridge" in installer
    assert "systemctl enable --now crew-rea-relay.service" in installer
    assert "crew.service" not in installer
    assert "wedding.service" not in installer

def test_partial_open_failure_still_attempts_target_cleanup(staged, monkeypatch):
    calls = []

    def fake_mcp(method, request_id=None, params=None, session=None, http_method="POST"):
        calls.append(method if http_method == "POST" else "DELETE")
        if method == "initialize":
            return {"id": 1, "result": {}}, "test-session"
        return None, None

    def fake_tool(name, args, request_id, session):
        calls.append(name)
        if name == "open_binary":
            raise rea_relay.RelayError("rea_open_binary_failed")
        return {"structuredContent": {}}

    monkeypatch.setattr(rea_relay, "mcp_request", fake_mcp)
    monkeypatch.setattr(rea_relay, "_tool", fake_tool)
    result = rea_relay.handle({"operation": "inspect_artifact", "filename": "example.apk"})
    assert not result["ok"]
    assert result["error"] == "rea_open_binary_failed"
    assert calls[-2:] == ["close_binary", "DELETE"]


def test_relay_socket_restart_fails_closed_for_unknown_entries():
    source = Path(rea_relay.__file__).read_text()
    assert "stat.S_ISSOCK(item.st_mode)" in source
    assert "item.st_uid != os.geteuid()" in source
    assert "except ConnectionRefusedError:" in source
    assert "REA relay socket is already serving" in source
