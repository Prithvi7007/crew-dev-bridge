"""Fail-closed handling of interrupted Git checkout during Wedding release.

All subprocess, socket, database and filesystem calls are mocked.
"""
from host import wedding_release_agent as release


def _simulate_checkout_failure(monkeypatch, *, rollback_fails=False):
    previous = "a" * 40
    target = "b" * 40
    calls = []
    states = []

    monkeypatch.setattr(release, "preflight", lambda: previous)
    monkeypatch.setattr(release, "target", lambda: target)
    monkeypatch.setattr(release, "ci_green", lambda candidate: candidate == target)
    monkeypatch.setattr(release, "allowed_changes", lambda a, b: 1)
    monkeypatch.setattr(release, "health", lambda: True)
    monkeypatch.setattr(release, "backup", lambda job: "fake-offline-dump")
    monkeypatch.setattr(release, "rehearse", lambda dump: None)
    monkeypatch.setattr(release, "verify_readiness", lambda: True)
    monkeypatch.setattr(release, "save", lambda data: states.append(data))
    monkeypatch.setattr(release, "report_phase",
                        lambda job, phase: calls.append(("phase", phase)))
    monkeypatch.setattr(release, "run",
                        lambda argv, **kwargs: calls.append(("service", tuple(argv))))
    monkeypatch.setattr(release, "postgres",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("Must not restore or modify live database")))

    def git(*args, **kwargs):
        calls.append(("git", args))
        if args[:2] == ("rev-parse", "FETCH_HEAD"):
            return target
        if args[:2] == ("reset", "--hard"):
            if args[2] == target or rollback_fails:
                raise release.Stop("command_failed")
        return ""

    monkeypatch.setattr(release, "git", git)
    release.execute("offline-test")
    return previous, target, calls, states


def test_failed_target_reset_attempts_rollback(monkeypatch):
    previous, target, calls, states = _simulate_checkout_failure(monkeypatch)
    resets = [event[1][2] for event in calls
              if event[0] == "git" and event[1][:2] == ("reset", "--hard")]
    assert resets == [target, previous]
    assert ("service", ("systemctl", "restart", "wedding.service")) in calls
    assert states[-1]["state"] == "failed"
    assert states[-1]["rolled_back"] is True
    assert states[-1]["error"] == "command_failed"


def test_failed_target_and_rollback_require_manual_recovery(monkeypatch):
    previous, target, calls, states = _simulate_checkout_failure(
        monkeypatch, rollback_fails=True)
    resets = [event[1][2] for event in calls
              if event[0] == "git" and event[1][:2] == ("reset", "--hard")]
    assert resets == [target, previous]
    assert states[-1]["state"] == "manual_recovery_required"
    assert states[-1]["error"] == "code_rollback_failed"
