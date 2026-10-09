import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from bridge import core


def git(workspace, *args):
    return subprocess.run(['git', '-C', str(workspace), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    root.mkdir()
    git(root, 'init', '-q', '-b', 'feature/test-badges')
    git(root, 'config', 'user.email', 'ci@example.invalid')
    git(root, 'config', 'user.name', 'CI')
    (root / 'README.md').write_text('hello\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'Initial')
    monkeypatch.setattr(core, 'WORKSPACE', root)
    monkeypatch.setattr(core, 'QUEUE', tmp_path / 'queue')
    monkeypatch.setattr(core, 'LOCK', tmp_path / 'queue' / 'lock')
    monkeypatch.setattr(core, 'AUDIT', tmp_path / 'audit' / 'events.jsonl')
    return root


def test_path_security(workspace):
    for path in ('../etc/passwd', '/etc/passwd', 'app/../../oops', '.git/config',
                 '.env', 'config/.env.prod', 'secrets/token', '.github/workflows/ci.yml'):
        with pytest.raises(core.BridgeError):
            core.safe_path(path)
    (workspace / 'outside').symlink_to('/etc/passwd')
    with pytest.raises(core.BridgeError):
        core.safe_path('outside')
    (workspace / 'sub').mkdir()
    (workspace / 'sub' / 'nested').symlink_to('/tmp', target_is_directory=True)
    with pytest.raises(core.BridgeError):
        core.safe_path('sub/nested/file')


def test_branch_security():
    for name in ('main', 'fix/test', 'feature/../main', 'feature/a//b',
                 'feature/a/.hidden', 'feature/a.', 'feature/A', 'feature/x/..'):
        with pytest.raises(core.BridgeError):
            core.validate_branch(name)
    assert core.validate_branch('feature/season-badges-foundation')


def test_optimistic_source_edits(workspace):
    original = core.read_file('README.md')
    with pytest.raises(core.BridgeError):
        core.write_file('README.md', 'new', 'wrong-sha')
    result = core.write_file('README.md', 'new content', original['sha256'])
    assert 'README.md' in result['changed_paths']
    with pytest.raises(core.BridgeError):
        core.write_file('README.md', 'old content', original['sha256'])
    assert core.read_file('README.md')['content'] == 'new content'
    core.write_file('app/new.py', 'print(1)\n', 'NEW')
    assert 'app/new.py' in core.status()['changes']


def test_snapshot_queue_is_nonexecuting(workspace):
    core.write_file('app/new.py', 'print(2)', 'NEW')
    job = core.start_check('pytest')
    ticket = json.loads((core.QUEUE / 'jobs' / (job['job_id']+'.json')).read_text())
    assert ticket['kind'] == 'pytest'
    assert ticket['fingerprint'] == core.fingerprint()
    assert (core.QUEUE / 'snapshots' / job['job_id'] / 'app/new.py').exists()
    assert core.check_status(job['job_id'])['state'] == 'queued_or_running'
    with pytest.raises(core.BridgeError):
        core.start_check('shell')


def test_fingerprint_changes_on_edits(workspace):
    before = core.fingerprint()
    r = core.read_file('README.md')
    core.write_file('README.md', 'hello again', r['sha256'])
    assert core.fingerprint() != before


def test_commit_requires_both_passing_checks(workspace):
    core.write_file('app/new.py', 'print(2)', 'NEW')
    with pytest.raises(core.BridgeError, match='Both pytest and react_build'):
        core.commit_and_push('Add a new feature')


def test_no_credentials_needed_to_read(workspace, monkeypatch):
    monkeypatch.setattr(core, 'TOKEN_FILE', workspace / 'nonexistent')
    assert core.status()['production_access'] is False
    with pytest.raises(core.BridgeError, match='credential not provisioned'):
        core.github_request('GET', 'git/ref/heads/main')


def test_apply_patch_and_reject_traversal(workspace):
    patch = ('diff --git a/README.md b/README.md\n'
             'index b6fc4c6..44e4c44 100644\n'
             '--- a/README.md\n+++ b/README.md\n'
             '@@ -1 +1 @@\n-hello\n+goodbye\n')
    applied = core.apply_patch(patch)
    assert applied['applied_paths'] == ['README.md']
    assert core.read_file('README.md')['content'] == 'goodbye\n'
    bad = patch.replace('README.md', '../etc/passwd')
    with pytest.raises(core.BridgeError):
        core.apply_patch(bad)
    with pytest.raises(core.BridgeError):
        core.apply_patch(patch + 'new file mode 120000\n')
