"""Fixed-target wedding code release security contracts: no real host/DB writes."""
import pytest

from bridge import wedding_release_gateway as gateway
from host import wedding_release_agent as release


def test_gateway_has_zero_argument_operation_allowlist(monkeypatch):
    monkeypatch.setattr(gateway, "SOCKET", "/no/such/socket")
    assert gateway.OPS == {"capabilities", "plan", "start", "status"}
    for operation in ("bash", "rm -rf /", "deploy latest", "start; dropdb", None):
        assert gateway.call(operation)["error"] == "operation_not_allowed"
    assert gateway.call("start")["error"] == "wedding_release_agent_unavailable"


def test_server_does_not_accept_commit_database_path_or_flags():
    for request in (
        {"operation": "start", "sha": "a" * 40},
        {"operation": "start", "approved": True},
        {"operation": "start", "database": "crew_prod"},
        {"operation": "plan", "path": "/etc/shadow"},
        {"operation": "shell"}, {}, [], None,
    ):
        assert release.dispatch(request)["error"] == "operation_not_allowed"
    caps = release.dispatch({"operation": "capabilities"})
    assert caps["ok"] and caps["target"] == "main"
    assert caps["migration_policy"] == "code_only"


def test_schema_and_runtime_changes_blocked(monkeypatch):
    paths = ["app/models/rsvp.py", "migrations/versions/new.py",
             "scripts/apply_schema.py", "requirements.txt",
             "app/config.py", ".github/workflows/wedding-ci.yml", ".env"]
    for path in paths:
        monkeypatch.setattr(release, "git", lambda *a, **k: path if a[0] == "diff" and "--name-only" in a else "")
        with pytest.raises(release.Stop, match="manual_review"):
            release.allowed_changes("a" * 40, "b" * 40)


def test_normal_code_only_change_is_allowed(monkeypatch):
    def fake_git(*args, **kwargs):
        return "app/static/css/home.css" if "--name-only" in args else ""
    monkeypatch.setattr(release, "git", fake_git)
    assert release.allowed_changes("a" * 40, "b" * 40) == 1


def test_ci_check_is_pinned_and_fails_closed(monkeypatch):
    assert release.ci_green("not-a-commit") is False
    monkeypatch.setattr(release, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")))
    assert release.ci_green("a" * 40) is False


def test_release_plan_stops_if_checkout_unknown(monkeypatch):
    monkeypatch.setattr(release, "preflight", lambda: (_ for _ in ()).throw(release.Stop("invalid_checkout")))
    assert release.plan() == {"ok": False, "error": "invalid_checkout"}


def test_no_automatic_db_restoration_or_deploy_arguments():
    assert release.DB == "wedding_db"
    assert release.SCRATCH_DB != release.DB
    assert release.SERVICE == "wedding.service"
    assert release.BASE == "main"
