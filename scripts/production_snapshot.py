#!/usr/bin/env python3
"""Fixed, read-only production checks run locally by a restricted timer.

Produces only status codes and counts, never stdout/stderr from Git, service
logs, environment variables, DB credentials, tracked filenames or game data.
Requires NO GitHub token, database login, network, or elevated privileges.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PRODUCTION = Path('/opt/crew')
OUTPUT_DIR = Path('/var/lib/crew-dev-bridge/diagnostics')
HEALTH_SOCKET = '/run/crew/crew.sock'
SERVICE_NAMES = ('crew', 'nginx', 'docker', 'crew-tunnel')
RUN_ENV = {
    'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LC_ALL': 'C',
    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
    'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0',
}


def _run(command: list[str], timeout: float = 5.0):
    try:
        return subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout, env=RUN_ENV, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _service_states() -> dict:
    allowed = {'active', 'inactive', 'failed', 'activating', 'deactivating'}
    result = {}
    for name in SERVICE_NAMES:
        cmd = _run(['/usr/bin/systemctl', 'is-active', name])
        state = cmd.stdout.strip() if cmd is not None else ''
        result[name] = state if state in allowed else 'unknown'
    return result


def _git_status() -> dict:
    prefix = ['/usr/bin/git', '-C', str(PRODUCTION),
              '-c', 'core.hooksPath=/dev/null', '-c', 'core.pager=cat']
    commit = _run(prefix + ['rev-parse', 'HEAD'])
    branch = _run(prefix + ['symbolic-ref', '--quiet', '--short', 'HEAD'])
    changed = _run(prefix + ['status', '--porcelain=v1', '--untracked-files=normal'], timeout=8)
    sha = commit.stdout.strip() if commit and commit.returncode == 0 else None
    if not (isinstance(sha, str) and len(sha) == 40 and all(c in '0123456789abcdef' for c in sha)):
        sha = None
    ref = branch.stdout.strip() if branch and branch.returncode == 0 else None
    if ref and (len(ref) > 80 or not all(c.isascii() and (c.isalnum() or c in '_./-') for c in ref)):
        ref = None
    dirty = bool(changed.stdout) if changed and changed.returncode == 0 else None
    return {'git_commit': sha, 'git_branch': ref, 'tracked_or_untracked_changes': dirty}


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self):
        super().__init__('localhost', timeout=3)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(3)
        self.sock.connect(HEALTH_SOCKET)


def _health_status() -> int | None:
    connection = _UnixHTTPConnection()
    try:
        connection.request('GET', '/health', headers={'Host': 'cadacrew.fun'})
        response = connection.getresponse()
        response.read(512)
        return int(response.status)
    except (OSError, http.client.HTTPException, ValueError):
        return None
    finally:
        connection.close()


def _host_resources() -> dict:
    try:
        info = os.statvfs(PRODUCTION)
        disk = round(info.f_bavail * info.f_frsize / (1024 ** 3), 2)
    except OSError:
        disk = None
    memory = None
    try:
        with open('/proc/meminfo', encoding='ascii') as handle:
            for line in handle:
                if line.startswith('MemAvailable:'):
                    memory = int(line.split()[1]) // 1024
                    break
    except (OSError, ValueError, IndexError):
        pass
    return {'disk_available_gib': disk, 'memory_available_mib': memory}


def collect_snapshot() -> dict:
    production = _git_status()
    production['health_http_status'] = _health_status()
    return {
        'schema': 1,
        'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
        'services': _service_states(),
        'production': production,
        'host': _host_resources(),
    }


def write_snapshot(snapshot: dict, directory: Path = OUTPUT_DIR) -> None:
    """Replace within a directory bind mount (NOT a mounted individual file)."""
    # This directory must exist and be owned by the 'crew' system account,
    # with group read/traverse for the bridge's numeric GID 10001, no group write.
    if not directory.is_dir() or directory.is_symlink():
        raise RuntimeError('Diagnostics output directory is not provisioned')
    fd, name = tempfile.mkstemp(prefix='.status-', dir=directory)
    try:
        os.fchmod(fd, 0o640)
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(snapshot, output, sort_keys=True, separators=(',', ':'))
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, directory / 'status.json')
    finally:
        if os.path.exists(name):
            os.unlink(name)


if __name__ == '__main__':
    write_snapshot(collect_snapshot())
