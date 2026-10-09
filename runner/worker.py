"""Offline test worker. Never receives the GitHub token or production mounts."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

QUEUE = Path('/queue')
ALLOWED = {'pytest', 'react_build'}
TEST_UID = 10002
TEST_GID = 10002


def _drop_test_privileges():
    """Executed in the child immediately before launching untrusted repository code."""
    os.setgroups([])
    os.setgid(TEST_GID)
    os.setuid(TEST_UID)


def _assign_to_test_user(root: Path):
    # Copies are fresh ordinary files from a server-controlled snapshot.
    for folder, dirs, files in os.walk(root, followlinks=False):
        os.chown(folder, TEST_UID, TEST_GID, follow_symlinks=False)
        for name in files:
            os.chown(os.path.join(folder, name), TEST_UID, TEST_GID, follow_symlinks=False)
        for name in dirs:
            target = os.path.join(folder, name)
            if os.path.islink(target):
                os.lchown(target, TEST_UID, TEST_GID)


def _run_check(cmd, cwd, env):
    process = subprocess.Popen(
        cmd, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, preexec_fn=_drop_test_privileges,
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=210)
        return process.returncode, output[-18000:]
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate()
        return 124, 'Time limit exceeded (210 seconds). ' + output[-1500:]



def execute(ticket: dict) -> dict:
    job_id = ticket['job_id']
    kind = ticket['kind']
    if kind not in ALLOWED or not isinstance(job_id, str) or len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id):
        raise ValueError('Invalid job')
    snapshot = QUEUE / 'snapshots' / job_id
    if not snapshot.is_dir():
        raise ValueError('Missing source snapshot')
    with tempfile.TemporaryDirectory(prefix='crew-check-') as tmp:
        working = Path(tmp) / 'source'
        # Running builds/tests only inside the unprivileged, network-disabled worker.
        shutil.copytree(snapshot, working, symlinks=False)
        env = {'HOME': tmp, 'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'),
               'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(working),
               'CI': '1', 'CREW_ENV': 'development'}
        if kind == 'pytest':
            cmd, cwd = ['python', '-m', 'pytest', '-q'], working
        else:
            frontend = working / 'frontend'
            (frontend / 'node_modules').symlink_to('/opt/frontend/node_modules', target_is_directory=True)
            cmd, cwd = ['npm', 'run', 'build'], frontend
        # The queue is mounted /queue with permissions 2770, owned by
        # 10001:10001. The child is 10002:10002 and cannot traverse it.
        # Only this temp checkout is given to the test UID.
        _assign_to_test_user(Path(tmp))
        code, output = _run_check(cmd, cwd, env)
        return {'job_id': job_id, 'branch': ticket['branch'], 'fingerprint': ticket['fingerprint'],
                'kind': kind, 'state': 'passed' if code == 0 else 'failed',
                'exit_code': code, 'output': output}


def main():
    if os.geteuid() != 0 or os.getegid() != 10001:
        raise RuntimeError('Runner supervisor must be 0:10001 to drop test privileges')
    os.umask(0o007)
    (QUEUE / 'results').mkdir(parents=True, exist_ok=True)
    while True:
        for job_path in sorted((QUEUE / 'jobs').glob('*.json')):
            if job_path.name.endswith('.tmp'):
                continue
            job_id = job_path.stem
            result_path = QUEUE / 'results' / (job_id + '.json')
            if result_path.exists():
                job_path.unlink(missing_ok=True)
                continue
            try:
                ticket = json.loads(job_path.read_text())
                result = execute(ticket)
            except Exception as exc:
                result = {'job_id': job_id, 'kind': 'unknown', 'state': 'failed',
                          'exit_code': 125, 'output': f'Worker failed: {type(exc).__name__}'}
            tmp = result_path.with_suffix('.tmp')
            tmp.write_text(json.dumps(result))
            os.replace(tmp, result_path)
            job_path.unlink(missing_ok=True)
            snapshot = QUEUE / 'snapshots' / job_id
            shutil.rmtree(snapshot, ignore_errors=True)
        time.sleep(2)

if __name__ == '__main__':
    main()
