"""Reusable release agent contracts: entirely offline, no VPS or database writes."""
import io
import json
from pathlib import Path
import socket
import threading

import pytest

from bridge import release_gateway
from host import release_agent as release


def test_gateway_only_accepts_fixed_release_operations(monkeypatch):
    calls = []
    monkeypatch.setattr(release_gateway, "SOCKET", "/no/such/socket")
    assert release_gateway.call("bash")["error"] == "operation_not_allowed"
    assert release_gateway.call("rm -rf /")["error"] == "operation_not_allowed"
    assert release_gateway.call("start")["error"] == "release_agent_unavailable"
    assert release_gateway.OPS == {"capabilities", "plan", "start", "status"}


def test_agent_never_accepts_client_supplied_commit_command_or_path():
    for payload in (
        {"operation": "start", "sha": "1" * 40},
        {"operation": "start", "command": "bash"},
        {"operation": "start", "database": "postgres"},
        {"operation": "start", "approved": True},
        {"operation": "plan", "path": "/etc/passwd"},
        {"operation": "repair"},
        {"operation": "execute_v15"},
        [],
        None,
    ):
        assert release.dispatch(payload)["ok"] is False
    supported = release.dispatch({"operation": "capabilities"})
    assert supported["ok"] and "start" in supported["operations"]


def test_expected_revision_and_migration_chain_filters_downgrade(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app/migrations.py").write_text('HEAD_REVISION = "v16_trophy_tour"\n')
    versions = tmp_path / "alembic/versions"
    versions.mkdir(parents=True)
    (versions / "v16.py").write_text(
        'revision = "v16_trophy_tour"\n'
        'down_revision = "v15_badges"\n'
        'def upgrade():\n    op.create_table("trophy_tour_completions")\n'
        'def downgrade():\n    op.drop_table("trophy_tour_completions")\n'
    )
    assert release._check_additive_migrations(tmp_path, "v15_badges") == ["v16_trophy_tour"]
    assert release._check_additive_migrations(tmp_path, "v16_trophy_tour") == []
    assert release._expected_revision(tmp_path) == "v16_trophy_tour"

    (versions / "v16.py").write_text(
        'revision = "v16_trophy_tour"\n'
        'down_revision = "v15_badges"\n'
        'def upgrade():\n    op.drop_table("profiles")\n'
        'def downgrade():\n    pass\n'
    )
    with pytest.raises(release.ReleaseError, match="non_additive"):
        release._check_additive_migrations(tmp_path, "v15_badges")
    (versions / "v16.py").write_text(
        'revision = "v16_trophy_tour"\\n'
        'down_revision = "v15_badges"\\n'
        'def upgrade():\\n    op.create_table("new_table")\\n'
        'def downgrade():\\n    pass\\n'
    )
    with pytest.raises(release.ReleaseError, match="invalid_migration_chain"):
        release._check_additive_migrations(tmp_path, "v14_seasons")


def test_rejects_unexpected_production_origin(monkeypatch):
    def git(*argv, **kwargs):
        if argv == ("remote", "get-url", "origin"):
            return "https://github.com/attacker/project.git"
        raise AssertionError("must fail before other git commands")
    monkeypatch.setattr(release, "_git", git)
    with pytest.raises(release.ReleaseError, match="unexpected_git_remote"):
        release._target()


def test_only_successful_expected_github_ci_is_accepted(monkeypatch):
    class FakeResponse:
        def __init__(self, runs):
            self.payload = json.dumps({"check_runs": runs}).encode()
        def __enter__(self):
            return io.BytesIO(self.payload)
        def __exit__(self, *_):
            return False
    captured = []
    def urlopen(req, **kwargs):
        captured.append(req.full_url)
        return FakeResponse([{"name": "test-and-audit", "status": "completed",
                              "conclusion": "success"}])
    monkeypatch.setattr(release.urllib_request, "urlopen", urlopen)
    assert release._ci_verified("a" * 40)
    assert "commits/" + "a" * 40 in captured[0]
    monkeypatch.setattr(release.urllib_request, "urlopen",
                        lambda *_args, **_kwargs: FakeResponse([
                            {"name": "test-and-audit", "status": "completed",
                             "conclusion": "failure"},
                            {"name": "other", "status": "completed",
                             "conclusion": "success"},
                        ]))
    assert not release._ci_verified("a" * 40)


def test_failed_ci_never_stages_or_changes_production(monkeypatch, tmp_path):
    monkeypatch.setattr(release, "STATE_DIR", tmp_path)
    monkeypatch.setattr(release, "STATE_FILE", tmp_path / "status.json")
    monkeypatch.setattr(release, "_target", lambda: ("a" * 40, "b" * 40))
    monkeypatch.setattr(release, "_ci_verified", lambda _: False)
    monkeypatch.setattr(release, "_stage", lambda _: pytest.fail("staged despite failed CI"))
    monkeypatch.setattr(release, "_backup", lambda _: pytest.fail("backup before CI"))
    monkeypatch.setattr(release, "_git", lambda *_a, **_k: pytest.fail("git write despite failed CI"))
    release._run_release("job-ci")
    result = release._read()
    assert result["state"] == "failed"
    assert result["error"] == "quality_gate_not_green"
    assert not result["backup_created"]


def test_invalid_db_target_is_rejected_without_attempting_commands(monkeypatch):
    class RootOwnedFixture:
        def lstat(self):
            from types import SimpleNamespace
            return SimpleNamespace(st_mode=0o100600, st_uid=0)
        def read_text(self):
            return "DATABASE_URL=postgresql://crew_app:secret@localhost:5432/postgres\\n"
    monkeypatch.setattr(release, "ENV_FILE", RootOwnedFixture())
    with pytest.raises(release.ReleaseError, match="database_scope_mismatch"):
        release._load_env()


@pytest.mark.skipif(not hasattr(socket, "SO_PEERCRED"), reason="Linux only")
def test_host_socket_enforces_bridge_uid(monkeypatch, tmp_path):
    path = tmp_path / "release.sock"
    monkeypatch.setattr(release, "HOST_UID", 99999999)
    with release.Server(str(path), release.Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.socket(socket.AF_UNIX) as conn:
                conn.connect(str(path))
                conn.sendall(b'{"operation":"start"}\n')
                result = json.loads(conn.recv(1024))
                assert result == {"ok": False, "error": "peer_not_authorized"}
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_release_request_contract_and_deployment_assets():
    root = Path(__file__).resolve().parents[1]
    server = (root / "bridge/server.py").read_text()
    compose = (root / "compose.yaml").read_text()
    agent = (root / "host/release_agent.py").read_text()
    for name in ("crew_release_capabilities", "crew_release_plan",
                 "crew_release_execute", "crew_release_status"):
        assert "def " + name in server
    assert "/run/crew-release:/run/crew-release:ro" in compose
    assert "shell=True" not in agent
    assert "SO_PEERCRED" in agent
    assert "pg_dump" in agent and "pg_restore" in agent
    assert "DB_SCRATCH" in agent and "health/ready" in agent
    assert "crew-release.service" in (root / "deploy/release/install.sh").read_text()


def test_counts_preserve_saved_game_activity():
    assert release._counts_preserved("14:117:14:9", "14:120:14:10")
    assert release._counts_preserved("14:117:14:9", "14:117:14:9")
    assert not release._counts_preserved("14:117:14:9", "14:116:14:9")
    assert not release._counts_preserved("14:117:14:9", "14:117:14")
    assert not release._counts_preserved("14:117:14:9", "bad")


def test_full_release_pipeline_offline_orders_backup_rehearsal_before_writes(
        monkeypatch, tmp_path):
    old_sha, merged_sha = "a" * 40, "b" * 40
    events = []
    monkeypatch.setattr(release, "STATE_DIR", tmp_path)
    monkeypatch.setattr(release, "STATE_FILE", tmp_path / "status.json")
    monkeypatch.setattr(release, "_target", lambda: (old_sha, merged_sha))
    monkeypatch.setattr(release, "_ci_verified", lambda _sha: True)
    monkeypatch.setattr(release, "_stage", lambda _sha: events.append("stage"))
    monkeypatch.setattr(release, "_check_release_files", lambda *_: events.append("check_assets"))
    monkeypatch.setattr(release, "_remove_stage", lambda: events.append("clean"))
    monkeypatch.setattr(release, "_load_env", lambda: ({}, None))
    monkeypatch.setattr(release, "_env_for_db",
                        lambda _base, _parsed, db, **_opts: {"database": db})
    monkeypatch.setattr(release, "_check_additive_migrations",
                        lambda _stage, _rev: ["v16_trophy_tour"])
    monkeypatch.setattr(release, "_expected_revision", lambda _path: "v16_trophy_tour")
    monkeypatch.setattr(release, "_counts", lambda _env: "14:117:14:9")

    applied = set()
    def migration_rev(env):
        return "v16_trophy_tour" if env["database"] in applied else "v15_badges"
    monkeypatch.setattr(release, "_revision", migration_rev)
    def fake_flask(_stage, env):
        events.append("migrate:" + env["database"])
        applied.add(env["database"])
    monkeypatch.setattr(release, "_flask_in", fake_flask)

    def fake_backup(_env):
        events.append("backup")
        path = tmp_path / "snapshot.dump"
        path.write_bytes(b"verified-backup")
        import hashlib
        path.with_suffix(".dump.sha256").write_text(
            hashlib.sha256(path.read_bytes()).hexdigest() + "  snapshot.dump\n")
        return path
    monkeypatch.setattr(release, "_backup", fake_backup)

    def fake_postgres(*args):
        events.append("postgres:" + args[0])
        if args[0] == "psql":
            return "0"
        return ""
    monkeypatch.setattr(release, "_postgres", fake_postgres)

    def fake_run(argv, **kwargs):
        events.append(Path(argv[0]).name + ":" + argv[1])
        return ""
    monkeypatch.setattr(release, "_run", fake_run)

    def fake_git(*args, **kwargs):
        events.append("git:" + args[0])
        return merged_sha
    monkeypatch.setattr(release, "_git", fake_git)
    monkeypatch.setattr(release, "_health", lambda: events.append("health"))
    release._run_release("offline-happy")
    assert release._read()["state"] == "passed"
    assert release._read()["database_revision"] == "v16_trophy_tour"
    assert events.index("backup") < events.index("migrate:" + release.DB_SCRATCH)
    assert events.index("migrate:" + release.DB_SCRATCH) < events.index("migrate:" + release.DB_PROD)
    assert events.index("migrate:" + release.DB_PROD) < events.index("git:reset")
    assert events.index("git:reset") < events.index("health")
    assert events[-1] == "clean"


def test_failed_rehearsal_does_not_mutate_production(monkeypatch, tmp_path):
    monkeypatch.setattr(release, "STATE_DIR", tmp_path)
    monkeypatch.setattr(release, "STATE_FILE", tmp_path / "status.json")
    monkeypatch.setattr(release, "_target", lambda: ("a" * 40, "b" * 40))
    monkeypatch.setattr(release, "_ci_verified", lambda _sha: True)
    monkeypatch.setattr(release, "_stage", lambda _sha: None)
    monkeypatch.setattr(release, "_check_release_files", lambda *_: None)
    monkeypatch.setattr(release, "_remove_stage", lambda: None)
    monkeypatch.setattr(release, "_load_env", lambda: ({}, None))
    monkeypatch.setattr(release, "_env_for_db",
                        lambda _b, _p, db, **_kw: {"database": db})
    monkeypatch.setattr(release, "_revision", lambda _env: "v15_badges")
    monkeypatch.setattr(release, "_check_additive_migrations",
                        lambda *_: ["v16_trophy_tour"])
    monkeypatch.setattr(release, "_expected_revision", lambda _: "v16_trophy_tour")
    monkeypatch.setattr(release, "_counts", lambda _: "14:117:14:9")
    monkeypatch.setattr(release, "_backup",
                        lambda _: (_ for _ in ()).throw(release.ReleaseError("backup_failed")))
    monkeypatch.setattr(release, "_git", lambda *_a, **_kw:
                        pytest.fail("no production git commands after failed backup"))
    monkeypatch.setattr(release, "_flask_in", lambda *_:
                        pytest.fail("no migrations after failed backup"))
    release._run_release("offline-failure")
    assert release._read()["state"] == "failed"
    assert release._read()["error"] == "backup_failed"


def test_frontend_asset_and_dependency_checks_fail_closed(monkeypatch, tmp_path):
    original = tmp_path / "live"
    candidate = tmp_path / "staging"
    for path in (original, candidate):
        (path / "frontend").mkdir(parents=True)
        (path / "requirements.txt").write_text("Flask==3.1.1\n")
        (path / "frontend/package.json").write_text("{}")
        (path / "frontend/package-lock.json").write_text("{}")
    monkeypatch.setattr(release, "ROOT", original)
    monkeypatch.setattr(release, "WORKTREE", candidate)
    monkeypatch.setattr(release, "_git",
                        lambda *_args: "frontend/src/player/main.jsx")
    with pytest.raises(release.ReleaseError, match="frontend_assets_not_committed"):
        release._check_release_files("a" * 40, "b" * 40)
    monkeypatch.setattr(release, "_git",
                        lambda *_args: "frontend/src/player/main.jsx\napp/static/react/player.js")
    release._check_release_files("a" * 40, "b" * 40)
    (candidate / "requirements.txt").write_text("arbitrary-upgrade\n")
    with pytest.raises(release.ReleaseError, match="dependency_change_requires_operator_review"):
        release._check_release_files("a" * 40, "b" * 40)


def test_root_owned_agent_uses_absolute_runuser_path(monkeypatch):
    """Restricted systemd PATH must never break user switching."""
    calls = []
    class Completed:
        returncode = 0
        stdout = "ok"
    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return Completed()
    monkeypatch.setattr(release.subprocess, "run", fake_run)
    result = release._run(["git", "status", "--porcelain"],
                          user="crew",
                          env={"PATH": "/usr/bin:/bin"})
    assert result == "ok"
    assert calls[0][0][:4] == ["/usr/sbin/runuser", "-u", "crew", "--"]
    assert calls[0][0][4:] == ["git", "status", "--porcelain"]


def test_missing_system_executable_reports_safe_actionable_error(monkeypatch):
    def fake_run(argv, **kwargs):
        raise FileNotFoundError(2, "No such file or directory",
                                "/usr/sbin/runuser")
    monkeypatch.setattr(release.subprocess, "run", fake_run)
    with pytest.raises(release.ReleaseError,
                       match="required_host_executable_missing:runuser"):
        release._run(["git", "rev-parse", "HEAD"], user="crew")


def test_migration_uses_absolute_runuser_path(monkeypatch, tmp_path):
    calls = []
    class Completed:
        returncode = 0
    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return Completed()
    monkeypatch.setattr(release.subprocess, "run", fake_run)
    release._flask_in(tmp_path, {"PATH": "/usr/bin:/bin"})
    assert calls[0][0][0] == release.RUNUSER
    assert calls[0][1]["cwd"] == tmp_path
