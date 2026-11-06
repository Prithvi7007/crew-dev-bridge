"""Career Intelligence repository isolation and check-policy tests."""
import json
import subprocess

import pytest

from bridge import core
from runner import worker


def git(path, *args):
    return subprocess.run(
        ['git', '-C', str(path), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.fixture
def career_workspace(tmp_path, monkeypatch):
    games = tmp_path / 'games'
    career = tmp_path / 'career'
    for path in (games, career):
        path.mkdir()
        git(path, 'init', '-q', '-b', 'feature/test-career')
        git(path, 'config', 'user.name', 'CI')
        git(path, 'config', 'user.email', 'test@example.invalid')
        (path / 'README.md').write_text('hello\n')
        git(path, 'add', '.')
        git(path, 'commit', '-qm', 'Initial')
    monkeypatch.setattr(core, 'WORKSPACE', games)
    monkeypatch.setattr(core, 'CAREER_WORKSPACE', career)
    monkeypatch.setattr(core, 'QUEUE', tmp_path / 'queue')
    monkeypatch.setattr(core, 'LOCK', tmp_path / 'queue' / 'workspace.lock')
    monkeypatch.setattr(core, 'AUDIT', tmp_path / 'audit' / 'events.jsonl')
    return games, career


def test_scoped_workspace_and_repository_id(career_workspace):
    games, career = career_workspace
    with core.repository_context('career-intelligence'):
        assert core.status()['repository'] == 'Prithvi7007/Career-intelligence'
        core.write_file('career-only.txt', 'private project', 'NEW')
        assert core._workspace() == career
    assert (career / 'career-only.txt').exists()
    assert not (games / 'career-only.txt').exists()
    with core.repository_context('crew_games'):
        assert core.status()['repository'] == core.REPO
    with pytest.raises(core.BridgeError, match='Unsupported repository'):
        with core.repository_context('Career-intelligence'):
            pass


def test_career_checks_and_cross_repo_job_isolation(career_workspace):
    with core.repository_context('career-intelligence'):
        ticket = core.start_check('career_test')
        job_id = ticket['job_id']
        record = json.loads((core.QUEUE / 'jobs' / (job_id + '.json')).read_text())
        assert record['kind'] == 'career_test'
        assert record['repository'] == core.CAREER_REPO
        assert core.check_status(job_id)['state'] == 'queued_or_running'
        assert core.start_check('career_syntax')['kind'] == 'career_syntax'
        with pytest.raises(core.BridgeError, match='Unsupported check kind'):
            core.start_check('pytest')
        with pytest.raises(core.BridgeError, match='Unsupported check kind'):
            core.start_check('react_build')
    with core.repository_context('crew_games'):
        with pytest.raises(core.BridgeError, match='different repository'):
            core.check_status(job_id)
        with pytest.raises(core.BridgeError, match='Unsupported check kind'):
            core.start_check('career_test')


def test_career_commit_requires_both_node_checks(career_workspace):
    with core.repository_context('career-intelligence'):
        core.write_file('career.txt', 'hello\n', 'NEW')
        with pytest.raises(core.BridgeError, match='Both career_test and career_syntax'):
            core.commit_and_push('test: require both checks')


def test_token_selection_is_repository_scoped(tmp_path, monkeypatch):
    crew = tmp_path / 'crew-token'
    wedding = tmp_path / 'wedding-token'
    career = tmp_path / 'career-token'
    crew.write_text('crew-marker')
    wedding.write_text('wedding-marker')
    career.write_text('career-marker')
    monkeypatch.setattr(core, 'TOKEN_FILE', crew)
    monkeypatch.setattr(core, 'WEDDING_TOKEN_FILE', wedding)
    monkeypatch.setattr(core, 'CAREER_TOKEN_FILE', career)
    sent = []

    class Response:
        status_code = 200
        content = b'{}'

        def json(self):
            return {}

    def fake_request(method, url, **kwargs):
        sent.append((url, kwargs['headers']['Authorization']))
        return Response()

    monkeypatch.setattr(core.httpx, 'request', fake_request)
    for name in ('crew_games', 'wedding', 'career-intelligence'):
        with core.repository_context(name):
            core.github_request('GET', 'git/ref/heads/main')
    assert sent[0] == ('https://api.github.com/repos/Prithvi7007/crew_games/git/ref/heads/main', 'Bearer crew-marker')
    assert sent[1] == ('https://api.github.com/repos/Prithvi7007/Wedding_Website_Final/git/ref/heads/main', 'Bearer wedding-marker')
    assert sent[2] == ('https://api.github.com/repos/Prithvi7007/Career-intelligence/git/ref/heads/main', 'Bearer career-marker')


def test_runner_rejects_cross_repo_check_kind():
    base = {'job_id': 'a' * 32, 'repository': 'Prithvi7007/Career-intelligence'}
    with pytest.raises(ValueError, match='Check kind does not match repository'):
        worker.execute({**base, 'kind': 'pytest'})
    with pytest.raises(ValueError, match='Check kind does not match repository'):
        worker.execute({**base, 'kind': 'wedding_syntax'})
