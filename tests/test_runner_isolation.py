"""Regression guards for the separate runner/test-user security boundary."""
from pathlib import Path

from runner import worker

ROOT = Path(__file__).resolve().parents[1]


def test_untrusted_commands_drop_user_and_groups(monkeypatch):
    seen = []
    monkeypatch.setattr(worker.os, 'setgroups', lambda x: seen.append(('groups', x)))
    monkeypatch.setattr(worker.os, 'setgid', lambda x: seen.append(('gid', x)))
    monkeypatch.setattr(worker.os, 'setuid', lambda x: seen.append(('uid', x)))
    worker._drop_test_privileges()
    assert seen == [('groups', []), ('gid', 10002), ('uid', 10002)]


def test_worker_run_uses_privilege_drop():
    source = (ROOT / 'runner/worker.py').read_text()
    assert 'preexec_fn=_drop_test_privileges' in source
    assert "os.killpg(process.pid, signal.SIGKILL)" in source
    assert 'if os.geteuid() != 0 or os.getegid() != 10001:' in source


def test_compose_isolation_and_localhost_only():
    yaml = (ROOT / 'compose.yaml').read_text()
    runner = yaml.split('  runner:', 1)[1]
    bridge = yaml.split('  runner:', 1)[0]
    assert 'user: "0:10001"' in runner
    assert 'cap_add: [CHOWN, SETUID, SETGID]' in runner
    assert 'network_mode: none' in runner
    assert 'read_only: true' in runner
    assert 'user: "10001:10001"' in bridge
    assert '127.0.0.1:8777:8777' in bridge
    assert '/opt/crew' not in yaml


def test_bootstrap_enforces_private_group_queue():
    boot = (ROOT / 'scripts/bootstrap.sh').read_text()
    assert 'chmod 2770 queue queue/jobs queue/results queue/snapshots' in boot
    assert 'chmod -R g+rwX queue' in boot
    assert 'chmod 0600 secrets/github_token' in boot
