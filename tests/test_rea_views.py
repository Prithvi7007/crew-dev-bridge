"""Offline view contracts: real relay and validation, doubled MCP transport."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from bridge import rea_gateway
from host import rea_relay, rea_store

PARENT = "ev_" + "a" * 64
SUMMARY = "ev_" + "b" * 64
PAGE = "ev_" + "c" * 64
NAME = "fixture.xapk"
RAW = b"PK fixture for view contracts"
SHA = hashlib.sha256(RAW).hexdigest()
CONTAINER = "/work/managed/" + NAME


def request(operation="vault_entries", **changes):
    payload = {"operation": operation, "filename": NAME,
               "expected_sha256": SHA, "offset": 0, "limit": 10}
    if operation == "vault_evidence":
        payload["collection"] = "nodes"
    return {**payload, **changes}


def evidence(normalized, evidence_id):
    return {
        "evidence_id": evidence_id,
        "subject": {"name": NAME, "digest": {"sha256": SHA},
                    "local_path": CONTAINER, "format": "zip",
                    "architecture": None},
        "provider": {"id": "rea-analysis-view",
                     "name": "REA analysis view projection", "version": "1"},
        "analysis_profile": None,
        "predicate_type": "rea.analysis-view",
        "operation": "inspect_analysis_view",
        "parameters": {"parent_evidence_id": PARENT, "view": normalized["view"]},
        "raw_result": None,
        "normalized_result": normalized,
        "confidence": "derived", "authority": "shipped-artifact",
        "environment": None,
        "limitations": ["Outer-container inventory only."],
        "relationship_coverage": None,
        "locations": [{"kind": "artifact-path", "path": CONTAINER}],
        "evidence_links": [PARENT],
    }


@pytest.fixture
def service(tmp_path, monkeypatch):
    root = tmp_path / "managed"
    root.mkdir()
    (root / ".uploads").mkdir()
    (root / NAME).write_bytes(RAW)
    monkeypatch.setattr(rea_store, "ROOT", root)
    state = {"events": [], "source_complete": True,
             "rows": {
                 "occurrences": [
                     {"occurrence_id": "occ_" + digit * 64,
                      "artifact_id": "art_" + digit * 64,
                      "parent_occurrence_id": None if i == 0 else "occ_" + "1" * 64,
                      "logical_path": path, "entry_kind": "file",
                      "artifact_format": "apk" if path.endswith(".apk") else "file",
                      "declared_size": 10 + i, "compressed_size": 5 + i,
                      "encrypted": False, "executable": False,
                      "hash_status": "verified"}
                     for i, (digit, path) in enumerate([
                         ("1", "example.base.apk"), ("2", "config.arm64.apk"),
                         ("3", "manifest.json")])
                 ],
                 "nodes": [
                     {"artifact_id": "art_" + "1" * 64, "format": "zip",
                      "sha256": "d" * 64, "size": 10, "architecture": None,
                      "content_state": "embedded"}
                 ],
                 "edges": [
                     {"edge_id": "edge_" + "1" * 64, "ordinal": 0,
                      "parent_artifact_id": "art_" + "1" * 64,
                      "relation": "contains", "occurrence_id": "occ_" + "2" * 64,
                      "producer": None}
                 ],
                 "integrity_contradictions": [],
             }}

    def mcp(method, request_id=None, params=None, session=None, http_method="POST"):
        state["events"].append((method, copy.deepcopy(params), session, http_method))
        if method == "initialize":
            return {"id": 1, "result": {}}, "view-session"
        if method != "tools/call":
            return None, None
        assert session == "view-session"
        tool = params["name"]
        args = params["arguments"]
        if tool == "open_binary":
            assert args == {"path": CONTAINER}
            return {"result": {"structuredContent": {}}}, None
        if tool == "close_binary":
            return {"result": {"structuredContent": {}}}, None
        shared = {
            "parent_evidence_id": PARENT, "parent_operation": "inspect_artifact",
            "parent_digest": "a" * 64, "view_digest": "e" * 64,
            "artifact": {"path": CONTAINER, "sha256": SHA},
            "limitations": ["Outer-container inventory only."],
            "unknowns": ["Runtime behavior is unverified."],
        }
        if tool == "inspect_artifact":
            assert args == {"integrity_policy": "fail", "detail": "summary"}
            value = evidence({
                **shared, "kind": "summary", "view": {"kind": "summary"},
                "coverage": {"status": "complete-within-view", "examined": 8,
                             "total": 8, "next_offset": None, "exhausted": True},
                "summary": {"source_complete": state["source_complete"]},
            }, SUMMARY)
            if "summary_mutation" in state:
                state["summary_mutation"](value)
            return {"result": {"structuredContent": value}}, None
        assert tool == "inspect_analysis_view"
        assert args["source"] == {"kind": "retained-evidence", "evidence_id": PARENT}
        view = args["view"]
        assert view["kind"] == "inventory"
        if state.get("page_error"):
            return {"result": {"isError": True, "content": []}}, None
        rows = state["rows"][view["collection"]]
        start, limit = view["offset"], view["limit"]
        items = copy.deepcopy(rows[start:start + limit])
        exhausted = start + len(items) >= len(rows)
        value = evidence({
            **shared, "kind": "inventory", "view": view, "items": items,
            "manifest": {"root_sha256": SHA},
            "inventory_evidence_ids": ["ev_" + "f" * 64],
            "source_complete": state["source_complete"],
            "source_coverage": {"scope": "container-entries",
                                "status": "complete" if state["source_complete"] else "partial"},
            "coverage": {"status": "page" if items else "empty",
                         "examined": len(items), "total": len(rows),
                         "next_offset": None if exhausted else start + len(items),
                         "exhausted": exhausted},
        }, PAGE)
        if "page_mutation" in state:
            state["page_mutation"](value)
        return {"result": {"structuredContent": value}}, None

    monkeypatch.setattr(rea_relay, "mcp_request", mcp)
    return state


def tool_names(service):
    return [params["name"] for method, params, _, _ in service["events"]
            if method == "tools/call"]


def test_entries_page_preserves_names_coverage_and_same_session(service):
    result = rea_relay.handle(request(limit=2))
    assert result["ok"], result
    assert result["sha256"] == SHA
    assert result["untrusted_target_data"] is True
    assert result["source_reinspected"] is True
    view = result["evidence"]["normalized_result"]
    assert [x["logical_path"] for x in view["items"]] == [
        "example.base.apk", "config.arm64.apk"]
    assert view["coverage"] == {"status": "page", "examined": 2, "total": 3,
                                "next_offset": 2, "exhausted": False}
    assert view["parent_evidence_id"] == PARENT
    assert result["evidence"]["evidence_id"] == PAGE
    assert tool_names(service) == [
        "open_binary", "inspect_artifact", "inspect_analysis_view", "close_binary"]
    assert service["events"][-1][3] == "DELETE"
    assert (service["events"][-1][2]) == "view-session"
    args = next(p["arguments"] for m, p, _, _ in service["events"]
                if m == "tools/call" and p["name"] == "inspect_analysis_view")
    assert "logical_path" in args["view"]["fields"]
    assert "format_metadata" not in args["view"]["fields"]


def test_entries_next_page_and_past_end(service):
    result = rea_relay.handle(request(offset=2, limit=2))
    view = result["evidence"]["normalized_result"]
    assert [x["logical_path"] for x in view["items"]] == ["manifest.json"]
    assert view["coverage"]["next_offset"] is None
    empty = rea_relay.handle(request(offset=3))["evidence"]["normalized_result"]
    assert empty["items"] == []
    assert empty["coverage"]["status"] == "empty"
    assert empty["coverage"]["total"] == 3


@pytest.mark.parametrize("collection", ["nodes", "edges", "integrity_contradictions"])
def test_selected_evidence_collections_are_preserved(service, collection):
    result = rea_relay.handle(request("vault_evidence", collection=collection))
    assert result["ok"], result
    view = result["evidence"]["normalized_result"]
    assert view["view"]["collection"] == collection
    assert view["items"] == service["rows"][collection]


@pytest.mark.parametrize("field,value", [
    ("offset", -1), ("offset", True), ("offset", "0"), ("offset", 1_000_001),
    ("offset", 0.5), ("limit", 0), ("limit", 26), ("limit", False),
    ("limit", "10"), ("limit", 1.5),
])
def test_invalid_pages_fail_before_mcp(service, field, value):
    result = rea_relay.handle(request(**{field: value}))
    assert result == {"ok": False, "error": "invalid_page"}
    assert not service["events"]


@pytest.mark.parametrize("extra", [
    {"source": {"kind": "evidence-file", "path": "/etc/passwd"}},
    {"evidence_id": PARENT}, {"fields": ["raw_result"]},
    {"tool": "extract_artifact"},
])
def test_extra_sources_and_selectors_fail_closed(service, extra):
    assert rea_relay.handle(request(**extra)) == {
        "ok": False, "error": "invalid_request"}
    assert not service["events"]


@pytest.mark.parametrize("collection", ["occurrences", "summary", "raw", "../secrets", None])
def test_invalid_detail_collection_fails_before_mcp(service, collection):
    result = rea_relay.handle(request("vault_evidence", collection=collection))
    assert result == {"ok": False, "error": "invalid_collection"}
    assert not service["events"]


def test_wrong_expected_hash_prevents_opening_the_target(service):
    result = rea_relay.handle(request(expected_sha256="0" * 64))
    assert result == {"ok": False, "error": "sha256_mismatch"}
    assert not service["events"]


def test_malformed_expected_hash_prevents_file_inspection(service):
    result = rea_relay.handle(request(expected_sha256="not-a-digest"))
    assert result == {"ok": False, "error": "invalid_sha256"}
    assert not service["events"]


@pytest.mark.parametrize("stage", ["summary_mutation", "page_mutation"])
def test_changed_artifact_evidence_fails_and_closes_session(service, stage):
    service[stage] = lambda value: value["normalized_result"]["artifact"].update(
        sha256="0" * 64)
    result = rea_relay.handle(request())
    assert result == {"ok": False, "error": "rea_evidence_mismatch"}
    assert tool_names(service)[-1] == "close_binary"
    assert service["events"][-1][3] == "DELETE"


def test_invalid_parent_evidence_id_is_not_forwarded(service):
    service["summary_mutation"] = lambda value: value["normalized_result"].update(
        parent_evidence_id="../../secret")
    result = rea_relay.handle(request())
    assert result == {"ok": False, "error": "invalid_rea_evidence"}
    assert "inspect_analysis_view" not in tool_names(service)
    assert tool_names(service)[-1] == "close_binary"


def test_page_failure_still_closes_target_and_session(service):
    service["page_error"] = True
    result = rea_relay.handle(request())
    assert result == {"ok": False, "error": "rea_inspect_analysis_view_failed"}
    assert tool_names(service)[-1] == "close_binary"
    assert service["events"][-1][3] == "DELETE"


def test_byte_budget_shrinks_pages_without_cutting_json(service):
    for i, row in enumerate(service["rows"]["occurrences"]):
        row["logical_path"] = "界" * 900 + str(i)
    result = rea_relay.handle(request(limit=3))
    assert result["ok"], result
    assert len(json.dumps(result, separators=(",", ":")).encode()) <= 12_000
    assert result["requested_limit"] == 3
    assert result["effective_limit"] == 1
    view = result["evidence"]["normalized_result"]
    assert len(view["items"]) == 1
    assert view["coverage"]["next_offset"] == 1
    assert view["coverage"]["total"] == 3
    assert result["truncated"] is False


def test_single_oversized_row_returns_explicit_error_and_cleans_up(service):
    service["rows"]["occurrences"][0]["logical_path"] = "X" * 20_000
    result = rea_relay.handle(request(limit=1))
    assert result == {"ok": False, "error": "rea_page_too_large"}
    assert tool_names(service)[-1] == "close_binary"
    assert service["events"][-1][3] == "DELETE"


def test_exhausted_page_preserves_incomplete_source(service):
    service["source_complete"] = False
    result = rea_relay.handle(request())
    assert result["ok"], result
    view = result["evidence"]["normalized_result"]
    assert view["coverage"]["exhausted"] is True
    assert view["source_complete"] is False
    assert view["source_coverage"]["status"] == "partial"
    assert view["unknowns"] == ["Runtime behavior is unverified."]


def test_gateway_new_operations_require_exact_argument_sets():
    assert rea_gateway.call("vault_entries", filename=NAME)["error"] == "invalid_arguments"
    assert rea_gateway.call("vault_evidence", filename=NAME,
                            expected_sha256=SHA, offset=0, limit=10)["error"] == "invalid_arguments"
    assert rea_gateway.call("vault_entries", filename=NAME,
                            expected_sha256=SHA, offset=0, limit=10,
                            source="anything")["error"] == "invalid_arguments"
