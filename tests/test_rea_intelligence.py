"""Integration and evidence-safety checks for the one-release REA intelligence lab."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import time
import zipfile

import pytest

from bridge import rea_gateway
from host import rea_relay

ROOT = Path(__file__).parents[1]
MODULE_ROOT = ROOT / "deploy" / "rea-lab"
spec = importlib.util.spec_from_file_location("rea_lab_intelligence_test", MODULE_ROOT / "intelligence.py")
intel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(intel)
spec_api = importlib.util.spec_from_file_location("rea_lab_full_api", MODULE_ROOT / "api.py")
lab = importlib.util.module_from_spec(spec_api)
spec_api.loader.exec_module(lab)


@pytest.fixture
def investigation(tmp_path, monkeypatch):
    root = tmp_path / "results"
    root.mkdir()
    job = "a" * 32
    job_root = root / job
    folder = job_root / "sources" / "dex_013" / "sources" / "com" / "ringapp"
    folder.mkdir(parents=True)
    (folder / "JawsLiveSession.java").write_text(
        "package com.ringapp.streaming;\n"
        "import com.ringapp.streaming.service.live.LiveSessionConnector;\n"
        "public class JawsLiveSession {\n"
        "  public void begin() { WebRtcE2eeHelper helper = null; }\n"
        "}\n"
    )
    (folder / "Camera.java").write_text(
        "package com.ringapp.streaming;\n"
        "import com.ringapp.streaming.service.webrtc.JawsLiveSession;\n"
        "public class Camera { public void start() {} }\n"
    )
    (job_root / "status.json").write_text(json.dumps(
        {"job_id": job, "mode": "android", "state": "completed", "started_epoch": 1}
    ))
    monkeypatch.setattr(lab, "RESULTS", root)
    return job, job_root


def test_index_build_search_graph_and_cited_investigator(investigation):
    _, root = investigation
    progress = []
    stats = intel.build_index(root, progress.append)
    assert stats["state"] == "ready"
    assert stats["files_indexed"] == 2
    assert stats["symbols"] >= 2
    assert stats["imports"] >= 2
    assert progress[-1]["state"] == "ready"
    hits = intel.indexed_search(root, "LiveSessionConnector")
    assert hits["matches"]
    assert hits["matches"][0]["line"] == 2
    assert hits["matches"][0]["file"].endswith("JawsLiveSession.java")
    graph = intel.dependency_graph(root, "LiveSessionConnector")
    assert graph["graph_type"] == "static_imports_not_runtime_calls"
    assert graph["edges"][0]["line"] == 2
    assert graph["edges"][0]["evidence"].endswith("JawsLiveSession.java:2")
    packet = intel.investigate(root, "WebRtcE2eeHelper", "security")
    assert packet["mode"] == "evidence_retrieval_for_chatgpt_reasoning"
    assert packet["target_data_sent_to_external_models"] is False
    assert any(s["category"] == "cryptography" for s in packet["heuristic_signals"])
    assert all(":" in s["evidence"] for s in packet["heuristic_signals"])


def test_index_rejects_untrusted_query_and_missing_db(investigation):
    _, root = investigation
    with pytest.raises(ValueError, match="index_not_ready"):
        intel.indexed_search(root, "Ring")
    intel.build_index(root)
    for bad in ("", "\n", "a", "x" * 241):
        with pytest.raises(ValueError):
            intel.indexed_search(root, bad)
    with pytest.raises(ValueError, match="invalid_focus"):
        intel.investigate(root, "WebRTC streaming", "__import__")
    with pytest.raises(ValueError, match="invalid_limit"):
        intel.dependency_graph(root, "Jaws", limit=100000)


def test_paginated_compact_summary_reports_partial_not_success():
    rows = [{"entry": f"classes{i}.dex", "exit_code": 3, "decompiled_files": 100,
             "out_of_memory": False, "log_tail": "secret shouldn't be returned"} for i in range(30)]
    report = {"artifact": "Ring.xapk", "mode": "android",
              "observations": {"jadx_exit_code": 1, "decompiled_files": 3000,
                               "dex_total": 30, "dex_results": rows}}
    result = intel.compact_report(report, 12, 12)
    assert result["analysis_result"] == "partial"
    assert result["dex_counts"]["decompilation_errors"] == 30
    assert result["total"] == 30 and result["next_offset"] == 24
    assert result["dex_results"][0]["entry"] == "classes12.dex"
    assert len(result["dex_results"]) == 12
    assert all("log_tail" not in row for row in result["dex_results"])
    assert intel.compact_report(report, 24, 12)["next_offset"] is None
    with pytest.raises(ValueError):
        intel.compact_report(report, -1, 12)
    with pytest.raises(ValueError):
        intel.compact_report(report, 0, 1000)


def test_manifest_and_native_inventory_are_structural_not_false_permissions(tmp_path):
    apk = tmp_path / "sample.apk"
    with zipfile.ZipFile(apk, "w") as stream:
        stream.writestr("AndroidManifest.xml", b"\x03\x00\x08\x00binary")
        stream.writestr("classes.dex", b"dex")
        stream.writestr("lib/arm64-v8a/libvideo.so", b"ELF")
        stream.writestr("resources.arsc", b"dummy")
    evidence = intel.apk_metadata(apk)
    assert evidence["manifest_present"] is True
    assert evidence["manifest_decoded"] is False
    assert evidence["permissions"] == "unverified_binary_manifest"
    assert evidence["native_abis"] == ["arm64-v8a"]
    assert evidence["native_library_count"] == 1
    assert evidence["root_dex_count"] == 1


def test_existing_evidence_is_indexable_without_rerunning_jadx(investigation):
    job, root = investigation
    assert lab.index_state(job)["state"] == "not_started"
    lab.intelligence().build_index(root)
    assert lab.index_state(job)["state"] == "ready"
    (root / "report.json").write_text(json.dumps({
        "artifact": "Ring.xapk", "mode": "android",
        "observations": {"jadx_exit_code": 1, "decompiled_files": 2,
                         "dex_results": [{"entry": "classes.dex", "exit_code": 3,
                                          "decompiled_files": 2}]}}))
    assert lab.compact_summary(job)["analysis_result"] == "partial"
    assert lab.ready_index(job) == root
    with pytest.raises(lab.LabError):
        lab.ready_index("../outside")


def test_dashboard_private_shell_and_relay_allowlists():
    html = (MODULE_ROOT / "dashboard.html").read_text()
    docker = (MODULE_ROOT / "Dockerfile").read_text()
    assert 'id="token"' in html
    assert "textContent" in html and "localStorage" not in html
    assert "intelligence.py dashboard.html" in docker
    for name in ("lab_summary", "lab_index", "lab_index_status",
                 "lab_index_search", "lab_graph", "lab_investigate"):
        assert name in rea_gateway.FIELDS
        assert name in rea_relay.ALLOWED_OPERATIONS
    assert rea_relay.handle({"operation": "lab_index", "job_id": "../etc/passwd"})["ok"] is False
    assert rea_gateway.call("lab_graph", job_id="a" * 32, query="x", url="http://example.com")["error"] == "invalid_arguments"


def test_progress_status_only_contains_bounded_job_evidence(investigation):
    job, root = investigation
    (root / "progress.json").write_text(json.dumps({
        "state": "running", "dex_total": 20, "dex_finished": 14,
        "current_dex": "classes15.dex", "files_recovered": 101984,
    }))
    data = json.loads((root / "progress.json").read_text())
    assert 0 <= data["dex_finished"] <= data["dex_total"]
    assert len(json.dumps(data)) < 1000
