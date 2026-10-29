"""Offline checks for the isolated REA Lite deployment package.

These tests do not build images, contact registries, call REA, or alter host state.
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "deploy" / "rea-lite"


def load_smoke():
    path = ROOT / "scripts" / "smoke_test.py"
    spec = importlib.util.spec_from_file_location("rea_lite_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rea_service_is_separate_and_loopback_only():
    manifest = (ROOT / "compose.yaml").read_text()
    assert "name: rea-lite" in manifest
    assert '"127.0.0.1:18765:8080"' in manifest
    rea_block = manifest.split("  rea:\n", 1)[1].split("\n  ingress:", 1)[0]
    ingress_block = manifest.split("  ingress:\n", 1)[1].split("\nnetworks:", 1)[0]
    assert "ports:" not in rea_block
    assert "      - rea_isolated" in rea_block
    assert "      - rea_access" in ingress_block
    assert "      - rea_isolated" in ingress_block
    assert "nginx-unprivileged:1.31.7-alpine3.24" in ingress_block
    assert "./nginx.conf:/etc/nginx/conf.d/default.conf:ro" in ingress_block
    assert "read_only: true" in ingress_block
    assert "internal: true" in manifest
    assert "read_only: true" in manifest
    assert "no-new-privileges:true" in manifest
    assert "cap_drop: [ALL]" in manifest
    assert "mem_limit: 1024m" in manifest
    assert "cpus: \"1.0\"" in manifest
    assert "./artifacts:/work/artifacts:ro" in manifest
    assert "/opt/crew" not in manifest
    assert "docker.sock" not in manifest
    assert ".env" not in manifest


def test_rea_image_pins_runtime_and_reduces_tool_catalog():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "node:24.22.1-bookworm-slim" in dockerfile
    assert "rea-agents@6.8.0" in dockerfile
    assert "USER node" in dockerfile
    assert "REA_MCP_TOOL_FAMILIES=artifact,managed,application,session" in dockerfile
    assert 'CMD ["rea","mcp","--http"' in dockerfile


def test_installer_is_explicit_and_refuses_replacement():
    installer = (ROOT / "scripts" / "install.sh").read_text()
    preflight = (ROOT / "scripts" / "preflight.sh").read_text()
    assert '[[ "$EUID" == 0 ]]' in installer
    assert '== "main"' in installer
    assert "status --porcelain" in installer
    assert 'DEST="/opt/rea-lite"' in installer
    assert '[[ -e "$DEST" || -L "$DEST" ]]' in installer
    assert "docker compose up -d rea ingress" in installer
    assert '"$SOURCE/nginx.conf" "$DEST/nginx.conf"' in installer
    assert "scripts/smoke_test.py --retries 15" in installer
    assert "Docker Engine >=28" in preflight
    assert "Port 127.0.0.1:18765 unavailable" in preflight
    assert "docker compose config --quiet" in preflight
    assert "docker compose down -v" not in installer


def test_smoke_supports_sse_responses():
    smoke = load_smoke()
    payload = b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'
    response = smoke.decode(payload, "text/event-stream")
    assert response["result"] == {"ok": True}


def test_smoke_uses_one_session_and_closes_it(monkeypatch):
    smoke = load_smoke()
    actions = []

    def fake_send(payload=None, session=None, method="POST"):
        if method == "DELETE":
            actions.append(("delete", session))
            return None, None
        body = json.loads(payload)
        actions.append((body["method"], session))
        if body["method"] == "initialize":
            return {"id": 1, "result": {"protocolVersion": smoke.PROTOCOL}}, "test-session"
        if body["method"] == "notifications/initialized":
            return None, None
        if body["method"] == "tools/list":
            return {"id": 2, "result": {"tools": [{"name": "binary_session"}]}}, None
        if body["method"] == "tools/call":
            assert body["params"] == {"name": "binary_session", "arguments": {}}
            return {"id": 3, "result": {"isError": False}}, None
        raise AssertionError(body["method"])

    monkeypatch.setattr(smoke, "send", fake_send)
    smoke.verify()
    assert actions == [
        ("initialize", None),
        ("notifications/initialized", "test-session"),
        ("tools/list", "test-session"),
        ("tools/call", "test-session"),
        ("delete", "test-session"),
    ]

def test_repair_fails_closed_and_does_not_replace_rea():
    repair = (ROOT / "scripts" / "repair-ingress.sh").read_text()
    nginx = (ROOT / "nginx.conf").read_text()
    assert 'ORIGINAL_MERGE="0c41d321bbeefd4287aa70102477a4ebac0729fe"' in repair
    assert 'cmp -s - "$DEST/compose.yaml"' in repair
    assert '[[ ! -e "$DEST/nginx.conf" && ! -L "$DEST/nginx.conf" ]]' in repair
    assert 'docker compose up -d --no-deps ingress' in repair
    assert 'docker compose up -d --no-deps rea' not in repair
    assert 'docker compose down' not in repair
    assert "listen 8080;" in nginx
    assert "location = /mcp" in nginx
    assert "proxy_pass http://rea:18765;" in nginx
    assert "proxy_buffering off;" in nginx
    assert "location /" in nginx
