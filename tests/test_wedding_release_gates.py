"""Additional Wedding V3 release fail-closed checks; no real system writes."""
from bridge import wedding_release_gateway as gateway
from host import wedding_release_agent as release


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *args):
        import json
        return json.dumps(self.payload).encode()


def test_ci_green_requires_exact_branch_commit_workflow_event_and_success(monkeypatch):
    sha = "a" * 40
    valid = {
        "name": "Wedding V3 CI", "head_sha": sha,
        "head_branch": "main", "event": "push",
        "status": "completed", "conclusion": "success",
    }
    for name, value in [
        ("name", "other"),
        ("head_sha", "b" * 40),
        ("head_branch", "v3-production"),
        ("event", "pull_request"),
        ("status", "in_progress"),
        ("conclusion", "failure"),
    ]:
        payload = dict(valid)
        payload[name] = value
        monkeypatch.setattr(release, "urlopen",
                            lambda *args, p=payload, **kwargs: FakeResponse({"workflow_runs": [p]}))
        assert release.ci_green(sha) is False

    monkeypatch.setattr(release, "urlopen",
                        lambda *args, **kwargs: FakeResponse({"workflow_runs": [valid]}))
    assert release.ci_green(sha) is True


def test_not_ready_release_never_launches(monkeypatch):
    monkeypatch.setattr(release, "plan", lambda: {"ok": True, "executable": False})
    monkeypatch.setattr(release, "status_state", lambda: {"ok": True, "state": "idle"})
    assert release.dispatch({"operation": "start"})["error"] == "preflight_not_ready"


def test_no_external_shell_or_arbitrary_endpoint():
    assert gateway.OPS == release.OPS
    assert release.API.endswith("/Prithvi7007/Wedding_Website_Final")
    assert release.BRIDGE_UID == 10001
    assert release.DB == "wedding_db"
    assert release.SCRATCH_DB != release.DB
