"""Offline contract tests for the restricted host gateway.

These tests never connect to a production database or run a VPS command.
"""
import os
import socket
import threading

import pytest

from bridge import host_gateway
from host import agent


def test_sql_gate_rejects_mutations_and_multiple_statements():
    good = "SELECT player_id, points FROM crew_reporting.weekly_scores WHERE week = %s"
    assert agent.validate_sql(good, [3], 50)
    for query in (
        "DELETE FROM crew_reporting.weekly_scores",
        "UPDATE crew_reporting.weekly_scores SET points = 5",
        "INSERT INTO crew_reporting.weekly_scores VALUES (1)",
        "WITH x AS (DELETE FROM users) SELECT * FROM x",
        "SELECT 1; DROP TABLE users",
        "SELECT 1 -- surprise",
        "SELECT /* exploit */ 1",
        "SELECT 1 INTO new_table",
        "SELECT * FROM scores FOR UPDATE",
    ):
        assert not agent.validate_sql(query, [], 50)
    assert not agent.validate_sql("SELECT 1", ["x" * 257], 50)
    assert not agent.validate_sql("SELECT 1", [], 101)
    assert not agent.validate_sql("SELECT 1", [], True)


def test_sql_without_reporting_credential_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    assert agent.execute_db("SELECT 1", [], 1) == {
        "ok": False, "error": "reporting_database_not_configured"
    }


def test_host_command_allowlist_rejects_shell_and_host_mutations():
    assert agent.command_argv("uptime", []) == ["uptime", "-p"]
    assert agent.command_argv("memory", []) == ["free", "-m"]
    assert agent.command_argv("disk", ["/"]) == ["df", "-hP", "/"]
    assert agent.command_argv("service", ["crew.service"])[-1] == "crew.service"
    assert agent.command_argv("git_revision", ["crew_games"]) == [
        "git", "-C", "/opt/crew", "rev-parse", "HEAD"
    ]
    for command, args in (
        ("bash", ["-c", "id"]),
        ("sh", []),
        ("rm", ["-rf", "/"]),
        ("service", ["../../crew.service"]),
        ("service", ["crew.service; systemctl stop crew"]),
        ("git_revision", ["/etc/passwd"]),
        ("disk", ["/etc"]),
        ("uptime", ["--help"]),
        ("memory", ["-w"]),
        ("git_revision", ["crew_games", "--exec-path"]),
    ):
        with pytest.raises(ValueError):
            agent.command_argv(command, args)


def test_subprocess_uses_exact_argv_without_shell_or_inherited_env(monkeypatch):
    calls = []
    class FakeProcess:
        returncode = 0
        stdout = "up 10 hours\n"
    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return FakeProcess()
    monkeypatch.setattr(agent.subprocess, "run", fake_run)
    result = agent.execute_command("uptime", [])
    assert result["ok"] is True
    assert calls[0][0] == ["uptime", "-p"]
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["timeout"] == 5
    assert "CREDENTIALS_DIRECTORY" not in calls[0][1]["env"]


def test_mcp_client_rejects_bad_input_without_socket(monkeypatch):
    def unexpected(_payload):
        raise AssertionError("must not call gateway")
    monkeypatch.setattr(host_gateway, "_call", unexpected)
    assert not host_gateway.db_query("SELECT 1", [], 0)["ok"]
    assert not host_gateway.db_query("SELECT 1", [{"password": 1}], 1)["ok"]
    assert not host_gateway.vps_exec("bash", ["-c", "rm -rf /"])["ok"]
    assert not host_gateway.vps_exec("uptime", [0])["ok"]


def test_host_dispatch_does_not_accept_extra_fields(monkeypatch):
    def fake_command(command, args):
        return {"ok": True, "command": command}
    monkeypatch.setattr(agent, "execute_command", fake_command)
    assert agent.dispatch({"operation": "vps_exec", "command": "uptime", "args": []})["ok"]
    assert not agent.dispatch({"operation": "vps_exec", "command": "uptime", "args": [], "shell": True})["ok"]
    assert not agent.dispatch({"operation": "restart_crew", "command": "uptime", "args": []})["ok"]


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux peer credentials required")
def test_local_unix_gateway_checks_linux_uid_and_roundtrips(monkeypatch, tmp_path):
    sock_path = tmp_path / "host.sock"
    monkeypatch.setattr(agent, "BRIDGE_UID", os.getuid())
    monkeypatch.setattr(host_gateway, "SOCKET", str(sock_path))
    monkeypatch.setattr(agent, "execute_command",
                        lambda command, args: {"ok": True, "command": command, "safe": True})
    with agent.Gateway(str(sock_path), agent.Handler) as gateway:
        thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        thread.start()
        try:
            assert host_gateway.vps_exec("uptime", []) == {
                "ok": True, "command": "uptime", "safe": True
            }
            monkeypatch.setattr(agent, "BRIDGE_UID", os.getuid() + 1)
            result = host_gateway.vps_exec("uptime", [])
            assert result == {"ok": False, "error": "peer_not_authorized"}
        finally:
            gateway.shutdown()
            thread.join(timeout=5)
