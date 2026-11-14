"""Regression tests for source flexibility without weakening boundary controls."""
from pathlib import Path
import subprocess

import pytest

from bridge import core


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          text=True, capture_output=True).stdout.strip()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    root.mkdir()
    git(root, 'init', '-q', '-b', 'feature/test-flexible-source')
    git(root, 'config', 'user.email', 'tests@example.invalid')
    git(root, 'config', 'user.name', 'Bridge Tests')
    (root / 'README.md').write_text('baseline\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'baseline')
    monkeypatch.setattr(core, 'WORKSPACE', root)
    monkeypatch.setattr(core, 'QUEUE', tmp_path / 'queue')
    monkeypatch.setattr(core, 'LOCK', tmp_path / 'queue' / 'lock')
    monkeypatch.setattr(core, 'AUDIT', tmp_path / 'audit' / 'events.jsonl')
    return root


def test_more_than_thirty_source_files_allowed(workspace):
    folder = workspace / 'src'
    folder.mkdir()
    for i in range(45):
        (folder / f'feature_{i:03}.py').write_text(f'VALUE = {i}\n')
    paths = core.changed_paths()
    assert len(paths) == 45
    assert core.fingerprint()
    assert core.status()['changes'] == paths


def test_large_utf8_file_read_edit_and_fingerprint(workspace):
    value = 'π' * 140_000  # 280 KB in UTF-8, above old 250 KB limit
    written = core.write_file('src/large.py', '# ' + value + '\n', 'NEW')
    assert written['path'] == 'src/large.py'
    record = core.read_file('src/large.py')
    assert record['content'] == '# ' + value + '\n'
    before = core.fingerprint()
    update = core.write_file('src/large.py', '# ' + value + ' # updated\n', record['sha256'])
    assert update['sha256'] != record['sha256']
    assert core.fingerprint() != before


def test_large_patch_over_old_125kb_limit(workspace):
    lines = ['+' + ('a' * 1000) + str(i) + '\n' for i in range(140)]
    patch = ('diff --git a/src/large_patch.txt b/src/large_patch.txt\n'
             'new file mode 100644\n--- /dev/null\n+++ b/src/large_patch.txt\n'
             f'@@ -0,0 +1,{len(lines)} @@\n' + ''.join(lines))
    assert len(patch.encode()) > 125_000
    result = core.apply_patch(patch)
    assert result['applied_paths'] == ['src/large_patch.txt']
    assert (workspace / 'src/large_patch.txt').is_file()


def test_patch_can_span_many_files(workspace):
    patches = []
    for i in range(35):
        name = f'features/module_{i:03}.py'
        patches.append(f'diff --git a/{name} b/{name}\n'
                       'new file mode 100644\n'
                       '--- /dev/null\n'
                       f'+++ b/{name}\n'
                       '@@ -0,0 +1 @@\n+pass\n')
    result = core.apply_patch(''.join(patches))
    assert len(result['applied_paths']) == 35
    assert len(core.changed_paths()) == 35


def test_full_feature_commit_above_old_limit(workspace, monkeypatch):
    for i in range(36):
        path = workspace / f'module_{i:03}.py'
        path.write_text(f'FEATURE = {i}\n')
    branch_head = git(workspace, 'rev-parse', 'HEAD')
    remote_calls = []

    def fake_github(method, endpoint, payload=None):
        remote_calls.append((method, endpoint, payload))
        if (method, endpoint) == ('GET', 'git/ref/heads/feature/test-flexible-source'):
            return {'object': {'sha': branch_head}}
        if (method, endpoint) == ('GET', f'git/commits/{branch_head}'):
            return {'tree': {'sha': 'base-tree'}}
        if (method, endpoint) == ('POST', 'git/trees'):
            assert len(payload['tree']) == 36
            return {'sha': 'new-tree'}
        if (method, endpoint) == ('POST', 'git/commits'):
            return {'sha': 'fake-commit'}
        if (method, endpoint) == ('PATCH', 'git/refs/heads/feature/test-flexible-source'):
            return {'object': {'sha': 'fake-commit'}}
        raise AssertionError(f'unexpected GitHub operation: {method} {endpoint}')

    original_git = core._git
    def guarded_git(*args, **kwargs):
        if args[0] in {'fetch', 'reset'}:
            return ''  # no remote or workspace alteration in unit test
        return original_git(*args, **kwargs)

    monkeypatch.setattr(core, 'github_request', fake_github)
    monkeypatch.setattr(core, '_check_passed', lambda kind, current: True)
    monkeypatch.setattr(core, '_git', guarded_git)
    result = core.commit_and_push('One complete 36-file feature')
    assert result['commit_sha'] == 'fake-commit'
    assert len([c for c in remote_calls if c[:2] == ('POST', 'git/trees')]) == 1


def test_secret_and_symlink_blocks_still_apply(workspace):
    with pytest.raises(core.BridgeError):
        core.write_file('.env', 'SECRET=example', 'NEW')
    with pytest.raises(core.BridgeError):
        core.write_file('secrets/key.txt', 'x', 'NEW')
    (workspace / 'src').mkdir()
    (workspace / 'src' / 'shortcut').symlink_to('/etc/passwd')
    with pytest.raises(core.BridgeError):
        core.read_file('src/shortcut')
    for path, bound in core.FRONTEND_ARTIFACT_LIMITS.items():
        assert path.startswith('app/static/react/')
        assert bound > 0
