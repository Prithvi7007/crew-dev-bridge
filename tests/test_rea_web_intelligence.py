"""Offline Web Intelligence tests: no real network, no JavaScript execution."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import socket
from urllib.parse import urlsplit

import pytest

from bridge import rea_gateway
from host import rea_relay

ROOT = Path(__file__).parents[1]
SOURCES = ROOT / "deploy" / "rea-lab"


def load(name):
    spec = importlib.util.spec_from_file_location("rea_web_test_" + name, SOURCES / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


capture = load("web_capture")
web = load("web_intelligence")
intel = load("intelligence")
api = load("api")


def test_public_https_validation_blocks_local_intranet_and_tricky_urls():
    assert capture.canonical_url("https://www.humanizeai.pro/ad?utm_source=foo#about") == (
        "https://www.humanizeai.pro/ad?utm_source=foo")
    assert capture.safe_display_url("https://www.humanizeai.pro/ad?gclid=secret") == (
        "https://www.humanizeai.pro/ad")
    for url in (
        "http://example.com", "https://localhost/", "https://metadata.google.internal/",
        "https://127.0.0.1/", "https://192.168.1.4/", "https://[::1]/",
        "https://example.com:4443/admin", "https://user:pass@example.com/",
        "https://example.com/\r\nInjected: x",
        "https://example.com@127.0.0.1/",
        "file:///etc/passwd", "https://example.com." + "a" * 300,
        "javascript:alert(1)", "",
    ):
        with pytest.raises(capture.CaptureError):
            capture.canonical_url(url)


def test_dns_resolution_blocks_all_non_global_addresses(monkeypatch):
    monkeypatch.setattr(capture.socket, "getaddrinfo",
        lambda host, port, type: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
        ])
    with pytest.raises(capture.CaptureError, match="non_public_dns_address"):
        capture.public_addresses("example.com")
    monkeypatch.setattr(capture.socket, "getaddrinfo",
        lambda host, port, type: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", port))])
    assert capture.public_addresses("example.com") == [(socket.AF_INET, "1.1.1.1", 443)]


def test_same_origin_bounded_snapshot_without_network(monkeypatch):
    seen = []
    html = (b'<html><script src="/_next/static/asset.js?dpl=version"></script>'
            b'<script src="https://evil.example/x.js"></script>'
            b'<script src="/_next/static/asset.js?dpl=version"></script>'
            b'<script>window.inlineValue="/api/example"</script></html>')
    def fake_fetch(url, expected_host=None, limit=None, redirects=None):
        seen.append(url)
        if url.endswith("/ad"):
            return html, "text/html; charset=utf-8", "https://example.com/ad"
        assert expected_host == "example.com"
        return b'fetch("/api/process");console.log("safe")', "application/javascript", "https://example.com/_next/static/asset.js"
    monkeypatch.setattr(capture, "fetch", fake_fetch)
    result = capture.snapshot("https://example.com/ad")
    assert result["ok"]
    assert len([x for x in result["files"] if x["path"].endswith(".js")]) == 2
    assert sum("_next/static" in x for x in seen) == 1
    assert not any("evil.example" in x for x in seen)
    assert all("dpl=" not in f["url"] for f in result["files"])


@pytest.fixture
def captured(tmp_path):
    job_root = tmp_path / ("b" * 32)
    job_root.mkdir()
    fake = {
        "ok": True, "url": "https://example.com/ad",
        "files": [
            {"path": "page.html", "url": "https://example.com/ad",
             "content": '<html><script src="/_next/static/one.js"></script></html>'},
            {"path": "scripts/asset_000.js", "url": "https://example.com/_next/static/one.js",
             "content": "x" * 510 + 'fetch("/api/process");window.recaptchaToken=true;'
                         + 'var route="/api/user/plan";window.stripe="js.stripe.com";'},
        ],
        "asset_errors": [],
    }
    return job_root, fake


def test_saved_web_evidence_routes_technology_citations_and_full_text(captured):
    root, payload = captured
    report = web.analyze_and_save(root, payload)
    assert report["files_captured"] == 2
    assert report["api_endpoint_total"] == 2
    routes = {x["endpoint"]: x for x in report["api_endpoints"]}
    assert "/api/process" in routes and "/api/user/plan" in routes
    route = routes["/api/process"]
    assert route["file"] == "normalized/asset_000.js.txt"
    assert route["line"] == 2  # after 510 chars and "fetch("
    lines = (root / "sources" / route["file"]).read_text().splitlines()
    assert "/api/process" in lines[1]
    assert {v["name"] for v in report["technology_indicators"]} >= {"Next.js", "Stripe", "reCAPTCHA"}
    assert report["client_assets"][1]["sha256"]
    stats = intel.build_index(root)
    assert stats["state"] == "ready"
    assert any("normalized/" in x["file"] for x in intel.indexed_search(root, "process")["matches"])
    assert (root / "sources" / "scripts" / "asset_000.js").is_file()


def test_report_pagination_graph_and_version_comparison(captured):
    root, payload = captured
    report = web.analyze_and_save(root, payload)
    data = {"mode": "web", "artifact": "https://example.com/ad", "observations": report}
    summary = web.compact_web_report(data, 0, 1)
    assert summary["next_offset"] == 1 and len(summary["api_endpoints"]) == 1
    assert summary["mode"] == "web"
    route = web.web_graph(data, "process")
    assert route["edges"][0]["target_symbol"] == "/api/process"
    assert route["graph_type"] == "static_client_api_references_not_verified_calls"
    later = json.loads(json.dumps(data))
    later["observations"]["api_endpoints"].append({
        "endpoint": "/api/new", "file": "normalized/test.js.txt",
        "line": 2, "evidence": "normalized/test.js.txt:2",
    })
    later["observations"]["technology_indicators"].append({"name": "PostHog"})
    diff = web.compare_web_reports(data, later)
    assert diff["new_routes"] == ["/api/new"] and diff["new_technologies"] == ["PostHog"]
    with pytest.raises(ValueError, match="web_jobs_required"):
        web.compare_web_reports({"mode": "android"}, later)


def test_untrusted_capture_rejects_path_traversal_and_duplicates(captured):
    root, payload = captured
    payload["files"][1]["path"] = "../etc/passwd"
    with pytest.raises(ValueError, match="invalid_capture_path"):
        web.analyze_and_save(root, payload)
    # Fresh root, duplicate names must be rejected, no writes outside it.
    root2 = root.parent / ("c" * 32)
    root2.mkdir()
    payload["files"][1]["path"] = "page.html"
    with pytest.raises(ValueError, match="duplicate_capture_path"):
        web.analyze_and_save(root2, payload)
    assert not (root.parent / "etc" / "passwd").exists()


def test_web_job_submission_has_same_concurrency_gate_as_android(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "RESULTS", tmp_path)
    monkeypatch.setattr(api, "_ACTIVE", None)
    monkeypatch.setattr(api, "_INDEX_ACTIVE", None)
    launches = []
    class FakeThread:
        def __init__(self, target, args, daemon):
            launches.append((target, args, daemon))
        def start(self): pass
    monkeypatch.setattr(api.threading, "Thread", FakeThread)
    response = api.start_web_job("https://example.com/ad?utm_source=test")
    state = json.loads((tmp_path / response["job_id"] / "status.json").read_text())
    assert state["mode"] == "web" and state["artifact"] == "https://example.com/ad"
    assert launches[0][0] is api.run_web_job
    with pytest.raises(api.LabError, match="analysis_busy"):
        api.start_web_job("https://example.com/")
    monkeypatch.setattr(api, "_ACTIVE", None)


def test_existing_index_and_source_api_support_web_mode(captured, tmp_path, monkeypatch):
    root, payload = captured
    report = web.analyze_and_save(root, payload)
    intel.build_index(root)
    (root / "status.json").write_text(json.dumps({
        "job_id": root.name, "state": "completed", "mode": "web",
        "artifact": "https://example.com/ad"}))
    (root / "report.json").write_text(json.dumps({
        "mode": "web", "artifact": "https://example.com/ad", "observations": report}))
    (root / "index.json").write_text(json.dumps({"state": "ready", "files_indexed": 4}))
    monkeypatch.setattr(api, "RESULTS", root.parent)
    assert api.index_state(root.name)["state"] == "ready"
    assert api.compact_summary(root.name, 0, 1)["mode"] == "web"
    excerpt = api.source_excerpt(root.name, "normalized/asset_000.js.txt", 2)
    assert "/api/process" in "".join(x["text"] for x in excerpt["lines"])
    assert api.search_job(root.name, "/api/process")["matches"]


def test_worker_can_persist_mock_sidecar_response_offline(captured, monkeypatch, tmp_path):
    root, payload = captured
    monkeypatch.setattr(api, "RESULTS", root.parent)
    monkeypatch.setattr(api, "_ACTIVE", root.name)
    token = tmp_path / "token"
    token.write_text("abcd" * 16)
    monkeypatch.setattr(api, "TOKEN_FILE", token)
    monkeypatch.setattr(api.shutil, "disk_usage", lambda x: type("D", (), {"free": 10**12})())
    (root / "status.json").write_text(json.dumps({
        "job_id": root.name, "mode": "web", "state": "running", "artifact": "https://example.com/ad"}))
    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return json.dumps(payload).encode()
    def fake_urlopen(request, timeout):
        assert request.full_url == api.WEB_CAPTURE_URL
        assert request.headers["X-rea-lab-token"] == "abcd" * 16
        return FakeResponse()
    monkeypatch.setattr(api.urllib_request, "urlopen", fake_urlopen)
    api.run_web_job(root.name, "https://example.com/ad")
    status = api.read_job(root.name)
    assert status["state"] == "completed"
    assert status["analysis_result"] == "clean_static_snapshot"
    assert (root / "report.json").is_file()


def test_mcp_relay_web_operations_are_strictly_allowlisted(monkeypatch, tmp_path):
    captured_requests = []
    monkeypatch.setattr(rea_relay, "lab_request",
                        lambda op, fields: captured_requests.append((op, fields)) or {"ok": True})
    assert rea_relay.handle({"operation": "lab_web_submit", "url": "https://example.com"})["ok"]
    assert captured_requests[-1][0] == "lab_web_submit"
    assert rea_relay.handle({"operation": "lab_web_submit", "url": "https://example.com", "shell": "bash"})["error"] == "invalid_request"
    assert rea_relay.handle({"operation": "lab_web_compare", "job_id": "a" * 32, "other_job_id": "b" * 32})["ok"]
    assert rea_gateway.FIELDS["lab_web_submit"] == ("url",)
    assert rea_gateway.FIELDS["lab_web_compare"] == ("job_id", "other_job_id")
    assert rea_gateway.call("lab_web_submit", url="x" * 100001)["ok"] is False


def test_deployment_keeps_worker_offline_and_evidence_persistent():
    compose = (SOURCES / "compose.yaml").read_text()
    script = (SOURCES / "upgrade-web.sh").read_text()
    docker = (SOURCES / "Dockerfile").read_text()
    assert "networks: [lab_internal]" in compose
    assert "networks: [lab_access, lab_internal]" in compose
    assert "web_capture:" in compose and "read_only: true" in compose
    assert "web_capture:8767" in compose
    assert "COPY api.py intelligence.py web_intelligence.py dashboard.html" in docker
    assert "docker compose down" not in script
    assert "--force-recreate --no-deps web_capture lab" in script
    assert "crew.service" not in script and "wedding.service" not in script


def test_capture_is_pure_static_and_does_not_browse_private_apis():
    source = (SOURCES / "web_capture.py").read_text()
    assert "GET {target} HTTP/1.1" in source
    assert "getaddrinfo" in source and "ip.is_global" in source
    assert "ssl.create_default_context()" in source
    assert "expected_host" in source
    assert "eval(" not in source and "exec(" not in source
