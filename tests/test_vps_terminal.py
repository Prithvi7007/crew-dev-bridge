"""Offline contract tests for the opt-in, unrestricted VPS root terminal.

Test commands are fake; never run a root shell from this test suite.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time

import pytest

from host import vps_terminal as terminal
from bridge import vps_terminal_gateway as gateway


@pytest.fixture
def setup_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal, "STATE_DIR", tmp_path)
    monkeypatch.setattr(terminal, "_PENDING", {})
    monkeypatch.setattr(terminal, "_JOBS", {})
    monkeypatch.setattr(terminal, "_ACTIVE", None)
    monkeypatch.setattr(terminal, "_RUNNING", {})
    return tmp_path


def test_full_shell_syntax_is_allowed_but_not_executed_during_prepare(setup_terminal, monkeypatch):
    called = []
    monkeypatch.setattr(terminal.subprocess, "Popen", lambda *args, **kwargs: called.append(args))
    script = "cd /opt/crew-dev-bridge && git status --short && sudo systemctl status docker"
    result = terminal.dispatch({"operation": "prepare", "command": script,
                                "timeout_seconds": 300, "suppress_output": False})
    assert result["ok"] is True
    assert result["effective_identity"] == "root"
    assert result["command_sha256"] == hashlib.sha256(script.encode()).hexdigest()
    assert result["requires_explicit_chat_approval"] is True
    assert terminal._PENDING[result["ticket"]]["command"] == script
    assert called == []  # No shell invocation at the planning stage.


def test_execute_consumes_exact_digest_and_cannot_swap_command(setup_terminal, monkeypatch):
    planned = terminal.prepare("echo permitted", 45, False)
    assert not terminal.execute(planned["ticket"], "0" * 64)["ok"]
    assert planned["ticket"] in terminal._PENDING
    started = []
    class FakeThread:
        def __init__(self, target, args, daemon):
            started.append({"target": target, "args": args, "daemon": daemon})
        def start(self):
            pass
    monkeypatch.setattr(terminal.threading, "Thread", FakeThread)
    result = terminal.execute(planned["ticket"], planned["command_sha256"])
    assert result["state"] == "queued"
    assert started[0]["args"][1] == "echo permitted"
    assert started[0]["daemon"] is True
    assert terminal._PENDING == {}
    assert terminal.execute(planned["ticket"], planned["command_sha256"])["error"] == "unknown_or_mismatched_command"
    assert terminal.status(result["job_id"])["state"] == "queued"
    assert (setup_terminal / (result["job_id"] + ".json")).is_file()


def test_no_concurrent_root_commands_and_ticket_expires(setup_terminal, monkeypatch):
    first = terminal.prepare("true", 10, False)
    second = terminal.prepare("echo two", 10, False)
    monkeypatch.setattr(terminal, "_ACTIVE", "b" * 32)
    assert terminal.execute(first["ticket"], first["command_sha256"])["error"] == "vps_command_busy"
    assert first["ticket"] in terminal._PENDING
    terminal._PENDING[second["ticket"]]["expires"] = time.monotonic() - 1
    assert terminal.execute(second["ticket"], second["command_sha256"])["error"] == "unknown_or_mismatched_command"


def test_invalid_payloads_are_rejected_before_host_execution(setup_terminal):
    bad = [
        {"operation": "prepare", "command": "echo hi", "timeout_seconds": 300},
        {"operation": "execute", "ticket": "b" * 32, "command_sha256": "c" * 64, "shell": "/bin/sh"},
        {"operation": "execute", "ticket": "b" * 32, "command_sha256": "../../passwd"},
        {"operation": "status", "job_id": "../passwd"},
        {"operation": "prepare", "command": "", "timeout_seconds": 5, "suppress_output": False},
        {"operation": "prepare", "command": "x" * 8193, "timeout_seconds": 5, "suppress_output": False},
        {"operation": "prepare", "command": "echo ok", "timeout_seconds": 99999, "suppress_output": False},
        {"operation": "prepare", "command": "echo ok", "timeout_seconds": 5, "suppress_output": "no"},
        {"operation": "rm -rf /"},
        None, [],
    ]
    for payload in bad:
        assert terminal.dispatch(payload)["ok"] is False
    caps = terminal.dispatch({"operation": "capabilities"})
    assert caps["effective_identity"] == "root"
    assert caps["max_parallel_jobs"] == 1


def test_results_are_bounded_and_command_text_never_written_to_job_state(setup_terminal, monkeypatch):
    class FakePipe:
        def __init__(self, data):
            self.data = data
            self.used = False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, _size):
            if not self.used:
                self.used = True
                return self.data
            return b""

    class FakeProcess:
        def __init__(self, args, **kwargs):
            assert args[0:2] == ["/bin/bash", "-lc"]
            assert kwargs["start_new_session"] is True
            assert kwargs["stdin"] == subprocess.DEVNULL
            self.stdout = FakePipe(b"A" * 8000)
            self.stderr = FakePipe(b"B" * 7000)
            self.returncode = 0
        def wait(self, timeout):
            return self.returncode
        def poll(self):
            return self.returncode

    monkeypatch.setattr(terminal.subprocess, "Popen", FakeProcess)
    planned = terminal.prepare("echo private-secret-sample", 20, False)
    terminal._JOBS["c" * 32] = {"job_id": "c" * 32, "state": "queued",
                                 "command_sha256": planned["command_sha256"]}
    terminal._ACTIVE = "c" * 32
    terminal._execute_job("c" * 32, "echo private-secret-sample", 20, False)
    result = terminal.status("c" * 32)
    assert result["state"] == "completed"
    assert len(result["stdout_tail"]) == terminal.MAX_OUTPUT_BYTES
    assert result["stdout_truncated"] and result["stderr_truncated"]
    serialized = (setup_terminal / ("c" * 32 + ".json")).read_text()
    assert "private-secret-sample" not in serialized
    assert result["command_sha256"] == planned["command_sha256"]


def test_suppressed_output_never_returns_command_output(setup_terminal, monkeypatch):
    class FakePipe:
        def __init__(self): self.once = False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, _):
            if self.once: return b""
            self.once = True
            return b"sensitive stdout"
    class FakeProcess:
        def __init__(self, args, **kwargs):
            self.stdout = FakePipe()
            self.stderr = FakePipe()
        def wait(self, timeout): return 0
        def poll(self): return 0
    monkeypatch.setattr(terminal.subprocess, "Popen", FakeProcess)
    terminal._JOBS["d" * 32] = {"job_id": "d" * 32, "state": "queued", "command_sha256": "a" * 64}
    terminal._ACTIVE = "d" * 32
    terminal._execute_job("d" * 32, "echo sensitive", 5, True)
    result = terminal.status("d" * 32)
    assert result["stdout_tail"] == result["stderr_tail"] == ""
    assert result["output_suppressed"] is True


def test_bridge_tools_use_private_socket_without_shell_in_bridge(monkeypatch):
    calls = []
    monkeypatch.setattr(gateway, "_call", lambda payload: calls.append(payload) or {"ok": True})
    assert gateway.prepare("whoami", 10, False)["ok"]
    assert calls[-1] == {"operation": "prepare", "command": "whoami",
                         "timeout_seconds": 10, "suppress_output": False}
    assert gateway.execute("a" * 32, "b" * 64)["ok"]
    assert calls[-1]["operation"] == "execute" and "command" not in calls[-1]
    assert gateway.status("c" * 32)["ok"]
    assert gateway.prepare("x" * 8193)["error"] == "invalid_command_or_limits"
    assert gateway.execute("a" * 32, "wrong")["error"] == "invalid_ticket_or_digest"
    assert gateway.status("../etc/passwd")["error"] == "invalid_job_id"


def test_install_is_explicit_opt_in_with_private_unix_socket_only():
    root = Path(__file__).parents[1]
    unit = (root / "deploy/vps-terminal/crew-vps-terminal.service").read_text()
    installer = (root / "deploy/vps-terminal/install.sh").read_text()
    compose = (root / "compose.yaml").read_text()
    assert "User=root" in unit and "Group=986" in unit
    assert "RuntimeDirectory=crew-vps-terminal" in unit
    assert "docker compose up -d --build --force-recreate --no-deps bridge" in installer
    assert "/run/crew-vps-terminal:/run/crew-vps-terminal:ro" in compose
    assert "docker compose down -v" not in installer
    assert "crew.service" not in installer and "wedding.service" not in installer
