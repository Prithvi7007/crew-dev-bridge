"""REA Web Intelligence: preserve safe public-capture rejection diagnostics."""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

ROOT = Path(__file__).parents[1]
API = ROOT / "deploy" / "rea-lab" / "api.py"
spec = importlib.util.spec_from_file_location("rea_capture_errors_test", API)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


@pytest.mark.parametrize("body,expected", [
    ({"ok": False, "error": "public_http_status_403"}, "web_capture_public_http_status_403"),
    ({"ok": False, "error": "public_dns_resolution_failed"}, "web_capture_public_dns_resolution_failed"),
    ({"ok": False, "error": "tls_connection_failed"}, "web_capture_tls_connection_failed"),
    ({"ok": False, "error": "<script>secret=abc</script>"}, "web_capture_rejected"),
    ({"ok": False, "error": "x" * 110}, "web_capture_rejected"),
    ({"ok": False, "error": 300}, "web_capture_rejected"),
])
def test_capture_http_error_is_bounded_sanitized(tmp_path, monkeypatch, body, expected):
    job_id = "d" * 32
    root = tmp_path / job_id
    root.mkdir()
    (root / "status.json").write_text(json.dumps({
        "job_id": job_id, "mode": "web", "state": "running",
        "artifact": "https://example.com/",
    }))
    token = tmp_path / "token"
    token.write_text("abcd" * 16)
    monkeypatch.setattr(api, "RESULTS", tmp_path)
    monkeypatch.setattr(api, "_ACTIVE", job_id)
    monkeypatch.setattr(api, "TOKEN_FILE", token)
    monkeypatch.setattr(api.shutil, "disk_usage", lambda path: type("D", (), {"free": 10**12})())

    def reject(*args, **kwargs):
        raise HTTPError("http://web_capture:8767/capture", 400, "bad_request", {},
                        io.BytesIO(json.dumps(body).encode()))
    monkeypatch.setattr(api.urllib_request, "urlopen", reject)
    api.run_web_job(job_id, "https://example.com/")
    status = api.read_job(job_id)
    assert status["state"] == "failed"
    assert status["error"] == expected
    assert "secret" not in status["error"]
    assert api._ACTIVE is None
