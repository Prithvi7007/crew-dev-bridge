"""Wedding reporting security contracts; no real databases or services."""
import os
import socket
import threading

import pytest

from bridge import wedding_reporting_gateway as gateway
from host import wedding_diagnostics as agent


def test_invalid_reports_cannot_reach_host(monkeypatch):
    monkeypatch.setattr(gateway, "SOCKET", "/nonexistent/socket")
    for value in ("all_guests", "SELECT * FROM invitations", "overview; DROP TABLE rsvps", "", None):
        assert gateway.call(value)["ok"] is False
    assert set(gateway.REPORTS) == {"overview", "attendance", "health"}


def test_host_denies_arbitrary_sql_and_extra_fields(monkeypatch):
    monkeypatch.setattr(agent, "execute", lambda report: {"ok": True, "report": report})
    for message in (
        {"operation": "query", "report": "overview"},
        {"operation": "report", "report": "overview", "sql": "SELECT * FROM invitations"},
        {"operation": "report", "report": "admin_users"},
        {"operation": "report", "report": ["overview"]},
        {},
    ):
        assert not agent.dispatch(message)["ok"]
    assert agent.dispatch({"operation": "report", "report": "overview"})["ok"]


def test_reports_only_reference_aggregate_views():
    assert set(agent.REPORTS) == {"overview", "attendance", "health"}
    assert all("wedding_reporting." in sql for sql in agent.REPORTS.values())
    assert all("public." not in sql for sql in agent.REPORTS.values())
    assert all("SELECT" in sql and ";" not in sql for sql in agent.REPORTS.values())


def test_reporting_missing_credentials_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    assert agent.execute("overview") == {"ok": False, "error": "reporting_not_configured"}


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux only")
def test_gateway_peer_credential_check(monkeypatch, tmp_path):
    path = tmp_path / "diag.sock"
    monkeypatch.setattr(agent, "BRIDGE_UID", os.getuid())
    monkeypatch.setattr(gateway, "SOCKET", str(path))
    monkeypatch.setattr(agent, "execute", lambda report: {"ok": True, "report": report, "data": {}})
    with agent.Server(str(path), agent.Handler) as srv:
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            assert gateway.call("overview")["ok"]
            monkeypatch.setattr(agent, "BRIDGE_UID", os.getuid() + 1)
            assert gateway.call("overview")["error"] == "peer_not_authorized"
        finally:
            srv.shutdown()
            thread.join(timeout=5)
