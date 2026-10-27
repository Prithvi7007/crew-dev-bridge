"""No live HTTPS or databases: verify release HTTP asset gate and socket unit."""
import hashlib
from pathlib import Path

import pytest

from host import wedding_release_agent as release


def _prepare(tmp_path, monkeypatch, body=b":root { --cream: #fff0d5; }"):
    css = tmp_path / "app/static/css/tokens.css"
    css.parent.mkdir(parents=True)
    css.write_bytes(body)
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release, "STATIC_SMOKE_ASSETS", ("css/tokens.css",))
    return css


def test_public_assets_match_exact_disk_bytes(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    calls = []

    def served(rel, fingerprint):
        calls.append((rel, fingerprint))
        return b":root { --cream: #fff0d5; }"

    monkeypatch.setattr(release, "_local_https_static_asset", served)
    release.verify_public_assets()
    assert calls == [(
        "css/tokens.css",
        hashlib.sha256(b":root { --cream: #fff0d5; }").hexdigest()[:16],
    )]


@pytest.mark.parametrize("served", [b"403 Forbidden", b":root { --cream: old; }"])
def test_asset_mismatch_blocks_release(tmp_path, monkeypatch, served):
    _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(release, "_local_https_static_asset", lambda *_: served)
    with pytest.raises(release.Stop, match="public_asset_content_mismatch"):
        release.verify_public_assets()


def test_nginx_403_blocks_release(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)

    def refused(*_):
        raise release.Stop("public_asset_http_failure")

    monkeypatch.setattr(release, "_local_https_static_asset", refused)
    with pytest.raises(release.Stop, match="public_asset_http_failure"):
        release.verify_public_assets()


def test_missing_asset_fails_closed(tmp_path, monkeypatch):
    css = _prepare(tmp_path, monkeypatch)
    css.unlink()
    with pytest.raises(release.Stop, match="public_asset_missing"):
        release.verify_public_assets()


def test_stable_runtime_directory_survives_service_restart():
    unit = Path("deploy/wedding/wedding-release.service").read_text()
    assert "RuntimeDirectory=wedding-release" in unit
    assert "RuntimeDirectoryPreserve=yes" in unit
    assert "RuntimeDirectoryMode=0770" in unit
    assert "Group=986" in unit


def test_release_attempts_asset_delivery_both_sides_of_restart(monkeypatch):
    # This contract guards the same 403 regression at baseline, new version
    # and rollback. It never invokes the production release procedure.
    source = Path("host/wedding_release_agent.py").read_text()
    execute = source.split("def execute(job):", 1)[1].split(
        "def dispatch(", 1
    )[0]
    assert execute.count("verify_public_assets()") == 3
