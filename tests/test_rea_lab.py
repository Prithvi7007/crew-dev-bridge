"""Offline regression tests for the isolated REA Lab and CREW relay."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import time
import zipfile

import pytest

from bridge import rea_gateway
from host import rea_relay

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("rea_lab_api", ROOT / "deploy/rea-lab/api.py")
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    vault, reports, scratch = (tmp_path / n for n in ("vault", "reports", "scratch"))
    for folder in (vault, reports, scratch):
        folder.mkdir()
    monkeypatch.setattr(lab, "VAULT", vault)
    monkeypatch.setattr(lab, "RESULTS", reports)
    monkeypatch.setattr(lab, "_ACTIVE", None)
    return vault, reports, scratch


def archive(path, entries):
    with zipfile.ZipFile(path, "w") as file:
        for name, contents in entries.items():
            file.writestr(name, contents)


def test_names_traversal_and_zip_bomb_guards(workspace, monkeypatch):
    vault, _, scratch = workspace
    archive(vault / "okay.xapk", {"base.apk": b"dex", "config.arm64_v8a.apk": b"split"})
    assert lab.validate_input("okay.xapk").name == "okay.xapk"
    for bad in ("../okay.xapk", "sub/okay.xapk", ".hidden.apk", "cmd.sh", "/etc/passwd"):
        with pytest.raises(lab.LabError):
            lab.validate_input(bad)
    apk, entry = lab.select_apk(vault / "okay.xapk", scratch)
    assert entry == "base.apk" and apk.read_bytes() == b"dex"
    archive(vault / "malicious.zip", {"../etc/passwd": b"oops"})
    with pytest.raises(lab.LabError, match="unsafe_archive_entry"):
        lab.archive_inventory(vault / "malicious.zip")
    monkeypatch.setattr(lab, "MAX_UNPACK_BYTES", 2)
    with pytest.raises(lab.LabError, match="archive_entry_too_large|archive_unpacked_size_exceeded"):
        lab.archive_inventory(vault / "okay.xapk")


def test_inventory_job_evidence_persists_without_running_apk(workspace, monkeypatch):
    vault, reports, scratch = workspace
    archive(vault / "app.xapk", {"base.apk": b"data", "manifest.json": b"{}"})
    monkeypatch.setattr(lab.shutil, "disk_usage", lambda _: SimpleNamespace(free=10**11))
    actual_tmpdir = lab.tempfile.TemporaryDirectory
    monkeypatch.setattr(lab.tempfile, "TemporaryDirectory",
                        lambda **kwargs: actual_tmpdir(dir=scratch))
    result = lab.start_job("app.xapk", "inventory")
    job_id = result["job_id"]
    for _ in range(100):
        status = lab.read_job(job_id)
        if status["state"] != "running":
            break
        time.sleep(0.02)
    assert status["state"] == "completed", status
    report = json.loads((reports / job_id / "report.json").read_text())
    assert report["observations"]["entries_total"] == 2
    assert report["artifact_sha256"] == lab.digest(vault / "app.xapk")
    assert "Runtime execution" in report["unknowns"][0]
    with pytest.raises(lab.LabError):
        lab.read_job("../../etc/passwd")


def test_jadx_command_is_shell_free_and_search_has_grounded_lines(workspace, monkeypatch):
    vault, reports, scratch = workspace
    sample = vault / "app.apk"
    sample.write_bytes(b"dummy")
    out = reports / "job"
    out.mkdir()
    seen = []
    def fake_run(args, **kwargs):
        seen.append((args, kwargs))
        (out / "sources" / "SomeClass.java").write_text("class App {}\nLiveSessionConnector here\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(lab.subprocess, "run", fake_run)
    summary = lab.run_android(sample, scratch, out)
    assert summary["decompiled_files"] == 1
    assert seen[0][0][0] == lab.JADX and seen[0][1]["timeout"] == lab.TIMEOUT_SECONDS
    assert "shell" not in seen[0][1]
    job_id = "a" * 32
    folder = reports / job_id
    (folder / "sources").mkdir(parents=True)
    (folder / "sources" / "SomeClass.java").write_text("class App {}\nLiveSessionConnector here\n")
    lab.write_json(folder / "status.json", {"state": "completed", "mode": "android"})
    matches = lab.search_job(job_id, "LiveSessionConnector")["matches"]
    assert matches == [{"file": "SomeClass.java", "line": 2, "excerpt": "LiveSessionConnector here"}]
    with pytest.raises(lab.LabError):
        lab.search_job(job_id, "\n")


def test_relay_auth_allowlist_and_validation(monkeypatch, tmp_path):
    secret = tmp_path / "token"
    secret.write_text("a" * 64)
    monkeypatch.setattr(rea_relay, "LAB_TOKEN", secret)
    requests = []
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self, size): return b'{"ok":true,"job_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}'
    def intercept(req, timeout):
        requests.append(req)
        return Reply()
    monkeypatch.setattr(rea_relay.request, "urlopen", intercept)
    assert rea_relay.handle({"operation": "lab_submit", "filename": "Ring_3.115.0.xapk",
                             "mode": "android"})["ok"]
    assert requests[0].full_url == "http://127.0.0.1:18766/jobs"
    assert rea_relay.handle({"operation": "lab_status", "job_id": "b" * 32})["ok"]
    assert rea_relay.handle({"operation": "lab_status", "job_id": "../bad"})["error"] == "invalid_job_id"
    assert rea_relay.handle({"operation": "lab_search", "job_id": "b" * 32,
                             "query": "foo", "url": "https://example.com"})["error"] == "invalid_request"
    assert rea_relay.handle({"operation": "shell"})["error"] == "operation_not_allowed"
    assert {"lab_submit", "lab_report", "lab_search"} <= set(rea_gateway.FIELDS)


def test_deployment_isolated_and_no_unreviewed_execution():
    compose = (ROOT / "deploy/rea-lab/compose.yaml").read_text()
    docker = (ROOT / "deploy/rea-lab/Dockerfile").read_text()
    installer = (ROOT / "deploy/rea-lab/install.sh").read_text()
    assert "127.0.0.1:18766:8080" in compose and "internal: true" in compose
    assert "../rea-lite/managed:/vault:ro" in compose
    assert "cap_drop: [ALL]" in compose and "no-new-privileges:true" in compose
    assert "docker.sock" not in compose
    assert "545ea2be9c242511bc145755cf4bda2485ade42966e096f8b4d3da2a230e8974" in docker
    assert "sha256sum -c -" in docker
    assert "docker compose down -v" not in installer
    assert "crew.service" not in installer and "wedding.service" not in installer
    assert lab.MODES == ("inventory", "android", "native")
