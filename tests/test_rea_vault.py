"""Offline, hostile-input tests for the REA-only artifact vault."""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from bridge import rea_gateway
from host import rea_relay, rea_store


@pytest.fixture
def vault(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    root.mkdir()
    (root / ".uploads").mkdir()
    monkeypatch.setattr(rea_store, "ROOT", root)
    # The offline CI sandbox intentionally has less disk than the VPS.
    monkeypatch.setattr(rea_store.shutil, "disk_usage", lambda _: SimpleNamespace(free=10 * 1024**3))
    return root


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def test_verified_resumable_upload_file_visibility_and_explicit_delete(vault):
    raw = b"PK\x03\x04" + b"file data" * 200
    start = rea_store.begin_upload("Ring_3.115.0.xapk", len(raw), _sha(raw))
    assert start["max_chunk_bytes"] == 65536
    upload_id = start["upload_id"]
    assert rea_store.list_files()["files"] == []
    with pytest.raises(rea_store.StoreError, match="incorrect_offset"):
        rea_store.append_chunk(upload_id, 20, base64.b64encode(raw).decode())
    first = raw[:1200]
    rea_store.append_chunk(upload_id, 0, base64.b64encode(first).decode())
    assert rea_store.upload_status(upload_id)["offset"] == len(first)
    with pytest.raises(rea_store.StoreError, match="upload_incomplete"):
        rea_store.finish_upload(upload_id)
    rea_store.append_chunk(upload_id, len(first), base64.b64encode(raw[len(first):]).decode())
    result = rea_store.finish_upload(upload_id)
    assert result["sha256"] == _sha(raw)
    assert result["container_path"] == "/work/managed/Ring_3.115.0.xapk"
    assert rea_store.list_files()["file_count"] == 1
    assert rea_store.file_info("Ring_3.115.0.xapk")["sha256"] == _sha(raw)
    with pytest.raises(rea_store.StoreError, match="sha256_mismatch"):
        rea_store.delete_file("Ring_3.115.0.xapk", "0" * 64)
    assert (vault / "Ring_3.115.0.xapk").exists()
    result = rea_store.delete_file("Ring_3.115.0.xapk", _sha(raw))
    assert result == {"ok": True, "deleted": "Ring_3.115.0.xapk",
                      "sha256": _sha(raw), "permanent": True}
    assert not (vault / "Ring_3.115.0.xapk").exists()


def test_rejects_directory_traversal_symlinks_and_hardlinks(vault):
    (vault / "fixture.apk").write_bytes(b"sample")
    (vault / "symlink.zip").symlink_to(vault / "fixture.apk")
    os.link(vault / "fixture.apk", vault / "hardlink.jar")
    for name in ("../../etc/passwd", "/etc/passwd", "bad/name.apk", "bad\\name.apk",
                 ".env.apk", "file.sh", "name with spaces.apk", "x" * 101 + ".apk"):
        with pytest.raises(rea_store.StoreError):
            rea_store.file_info(name)
    for name in ("symlink.zip", "hardlink.jar"):
        with pytest.raises(rea_store.StoreError):
            rea_store.file_info(name)
    with pytest.raises(rea_store.StoreError):
        rea_store.delete_file("symlink.zip", _sha(b"sample"))


def test_wrong_hash_never_publishes_file(vault):
    raw = b"PK test"
    session = rea_store.begin_upload("foo.zip", len(raw), "0" * 64)
    rea_store.append_chunk(session["upload_id"], 0, base64.b64encode(raw).decode())
    with pytest.raises(rea_store.StoreError, match="sha256_mismatch"):
        rea_store.finish_upload(session["upload_id"])
    assert not (vault / "foo.zip").exists()
    rea_store.cancel_upload(session["upload_id"])
    assert not list((vault / ".uploads").iterdir())


def test_upload_chunk_validation_and_refusal_to_overwrite(vault):
    content = b"PK data"
    session = rea_store.begin_upload("foo.apk", len(content), _sha(content))
    uid = session["upload_id"]
    for offset, payload in [(0, "***"), (0, base64.b64encode(b"x" * 8).decode())]:
        with pytest.raises(rea_store.StoreError):
            rea_store.append_chunk(uid, offset, payload)
    rea_store.append_chunk(uid, 0, base64.b64encode(content).decode())
    rea_store.finish_upload(uid)
    with pytest.raises(rea_store.StoreError, match="filename_exists"):
        rea_store.begin_upload("foo.apk", len(content), _sha(content))
    assert (vault / "foo.apk").read_bytes() == content


def test_quota_and_malformed_size_fail_closed(vault, monkeypatch):
    for size in (-1, 0, 512 * 1024 * 1024 + 1, True, "10"):
        with pytest.raises(rea_store.StoreError, match="invalid_size"):
            rea_store.begin_upload("sample.xapk", size, "0" * 64)
    with pytest.raises(rea_store.StoreError, match="invalid_sha256"):
        rea_store.begin_upload("sample.zip", 1, "not-a-hash")
    monkeypatch.setattr(rea_store, "totals", lambda: (0, rea_store.MAX_TOTAL_BYTES, 0))
    with pytest.raises(rea_store.StoreError, match="vault_quota_exceeded"):
        rea_store.begin_upload("sample.zip", 1, "0" * 64)


def test_host_operations_do_not_accept_arbitrary_paths_or_urls(vault):
    assert rea_relay.handle({"operation": "vault_list"})["ok"]
    assert rea_relay.handle({"operation": "vault_info", "filename": "../secret.zip"})["error"] == "invalid_filename"
    assert rea_relay.handle({"operation": "vault_delete", "filename": "/etc/passwd",
                             "expected_sha256": "0" * 64})["error"] == "invalid_filename"
    assert rea_relay.handle({"operation": "vault_chunk", "upload_id": "x",
                             "offset": 0, "data_b64": "xx", "url": "http://localhost"})["error"] == "invalid_request"
    assert rea_relay.handle({"operation": "arbitrary_exec"})["error"] == "operation_not_allowed"
    assert rea_gateway.call("vault_delete", filename="artifact.apk")["error"] == "invalid_arguments"


def test_managed_inspection_uses_only_container_path(vault, monkeypatch):
    data = b"PK\x03\x04 sample"
    (vault / "ring.apk").write_bytes(data)
    actions = []

    def mcp(method, request_id=None, params=None, session=None, http_method="POST"):
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": 1, "result": {}}, "session-id"
        actions.append((method, http_method))
        return None, None

    def tool(name, params, request_id, session):
        actions.append((name, params))
        if name == "open_binary":
            assert params == {"path": "/work/managed/ring.apk"}
        if name == "inspect_artifact":
            return {"structuredContent": {"evidence_id": "ev_ring", "normalized_result": {"ok": True}}}
        return {"structuredContent": {}}

    monkeypatch.setattr(rea_relay, "mcp_request", mcp)
    monkeypatch.setattr(rea_relay, "_tool", tool)
    result = rea_relay.handle({"operation": "inspect_managed", "filename": "ring.apk"})
    assert result["ok"] and result["evidence_id"] == "ev_ring"
    assert ("open_binary", {"path": "/work/managed/ring.apk"}) in actions
    assert any(item[0] == "close_binary" for item in actions)
    assert ("", "DELETE") in actions


def test_boundary_config_does_not_touch_production_files():
    root = Path(__file__).parents[1]
    compose = (root / "deploy/rea-lite/compose.yaml").read_text()
    unit = (root / "deploy/rea-bridge/crew-rea-relay.service").read_text()
    installer = (root / "deploy/rea-vault/install.sh").read_text()
    assert "./managed:/work/managed:ro" in compose
    assert "ReadWritePaths=/opt/rea-lite/managed" in unit
    assert "ProtectSystem=strict" in unit
    assert "install -d -o nobody -g 1000 -m 2750" in installer
    assert "install -d -o nobody -g 1000 -m 2700" in installer
    assert "docker compose up -d --build --force-recreate --no-deps bridge" in installer
    assert "docker compose up -d --no-deps --force-recreate rea" in installer
    assert "docker compose down -v" not in installer
    assert "docker.sock" not in installer
