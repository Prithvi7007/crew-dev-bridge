"""Multi-repository isolation and scoping tests; no GitHub calls."""
import json
import subprocess

import pytest

from bridge import core


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repositories(tmp_path, monkeypatch):
    games = tmp_path / 'games'
    bridge = tmp_path / 'bridge'
    for path in (games, bridge):
        path.mkdir()
        git(path, 'init', '-q', '-b', 'feature/test-branch')
        git(path, 'config', 'user.name', 'CI')
        git(path, 'config', 'user.email', 'test@example.invalid')
        (path / 'example.txt').write_text('hello\n')
        git(path, 'add', '.')
        git(path, 'commit', '-qm', 'Initial')
    monkeypatch.setattr(core, 'WORKSPACE', games)
    monkeypatch.setattr(core, 'BRIDGE_WORKSPACE', bridge)
    monkeypatch.setattr(core, 'QUEUE', tmp_path / 'queue')
    monkeypatch.setattr(core, 'LOCK', tmp_path / 'queue' / 'workspace.lock')
    monkeypatch.setattr(core, 'AUDIT', tmp_path / 'audit' / 'events.jsonl')
    return games, bridge


def test_invalid_repository_is_rejected(repositories):
    with pytest.raises(core.BridgeError):
        with core.repository_context('../etc'):
            pass


def test_independent_workspaces_and_check_policy(repositories):
    games, bridge = repositories
    with core.repository_context('crew-dev-bridge'):
        assert core.status()['repository'] == 'Prithvi7007/crew-dev-bridge'
        core.write_file('bridge-only.txt', 'bridge', 'NEW')
        bridge_fingerprint = core.fingerprint()
        with pytest.raises(core.BridgeError):
            core.start_check('react_build')
    assert (bridge / 'bridge-only.txt').exists()
    assert not (games / 'bridge-only.txt').exists()
    with core.repository_context('crew_games'):
        assert core.status()['repository'] == core.REPO
        assert core.fingerprint() != bridge_fingerprint
        with pytest.raises(core.BridgeError):
            core.start_check('bridge_syntax')


def test_bridge_check_tickets_are_scoped(repositories):
    with core.repository_context('crew-dev-bridge'):
        result = core.start_check('bridge_syntax')
        job_id = result['job_id']
        ticket = json.loads((core.QUEUE / 'jobs' / (job_id + '.json')).read_text())
        assert ticket['repository'] == 'Prithvi7007/crew-dev-bridge'
        assert core.check_status(job_id)['state'] == 'queued_or_running'
    with core.repository_context('crew_games'):
        with pytest.raises(core.BridgeError):
            core.check_status(job_id)


def test_context_restored_after_exception(repositories):
    with pytest.raises(RuntimeError):
        with core.repository_context('crew-dev-bridge'):
            raise RuntimeError('test')
    assert core.status()['repository'] == core.REPO
