"""Read a host-generated, strictly allowlisted CREW production status snapshot.

The development bridge never executes commands on the production host and
cannot access the production source, sockets, database or service credentials.
"""
from __future__ import annotations

import json
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_FILE = Path('/run/crew-diagnostics/status.json')
MAX_BYTES = 4096
MAX_AGE_SECONDS = 900
SERVICES = ('crew', 'nginx', 'docker', 'crew-tunnel')
SERVICE_STATES = frozenset(('active', 'inactive', 'failed', 'activating', 'deactivating', 'unknown'))
_HEX_SHA = re.compile(r'^[0-9a-f]{40}$')
_SAFE_BRANCH = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_./-]{0,79}$')


def _not_ready(reason: str, checked_at: str | None = None) -> dict:
    result = {'available': False, 'reason': reason, 'production_access': 'snapshot_only'}
    if checked_at is not None:
        result['checked_at'] = checked_at
    return result


def read_snapshot(path: Path = SNAPSHOT_FILE, *, now: datetime | None = None) -> dict:
    """Return sanitized fields from a recent snapshot, never arbitrary file content.

    The path argument exists for unit tests. The MCP tool supplies no path.
    """
    try:
        if path.is_symlink():
            return _not_ready('invalid_snapshot')
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES or info.st_size < 2:
            return _not_ready('invalid_snapshot')
        with path.open('rb') as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return _not_ready('invalid_snapshot')
        data = json.loads(raw)
    except (OSError, ValueError, UnicodeError):
        return _not_ready('snapshot_missing_or_unreadable')
    if not isinstance(data, dict) or type(data.get('schema')) is not int or data.get('schema') != 1:
        return _not_ready('invalid_snapshot')
    stamp = data.get('checked_at')
    try:
        if not isinstance(stamp, str) or not stamp.endswith('Z'):
            raise ValueError('Invalid timestamp')
        dt = datetime.fromisoformat(stamp[:-1] + '+00:00')
        age = ((now or datetime.now(timezone.utc)) - dt).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return _not_ready('invalid_snapshot')
    if age < -60 or age > MAX_AGE_SECONDS:
        return _not_ready('stale_snapshot', stamp)

    services = data.get('services')
    production = data.get('production')
    host = data.get('host')
    if not isinstance(services, dict) or not isinstance(production, dict) or not isinstance(host, dict):
        return _not_ready('invalid_snapshot')
    if set(services) != set(SERVICES) or any(not isinstance(services[k], str) or services[k] not in SERVICE_STATES for k in SERVICES):
        return _not_ready('invalid_snapshot')

    commit = production.get('git_commit')
    if commit is not None and (not isinstance(commit, str) or not _HEX_SHA.fullmatch(commit)):
        return _not_ready('invalid_snapshot')
    branch = production.get('git_branch')
    if branch is not None and (not isinstance(branch, str) or not _SAFE_BRANCH.fullmatch(branch)):
        return _not_ready('invalid_snapshot')
    changed = production.get('tracked_or_untracked_changes')
    if changed is not None and type(changed) is not bool:
        return _not_ready('invalid_snapshot')
    http_status = production.get('health_http_status')
    if http_status is not None and (type(http_status) is not int or not (100 <= http_status <= 599)):
        return _not_ready('invalid_snapshot')
    free_gib = host.get('disk_available_gib')
    memory_mib = host.get('memory_available_mib')
    if free_gib is not None and (type(free_gib) not in (int, float) or not (0 <= free_gib <= 1_000_000)):
        return _not_ready('invalid_snapshot')
    if memory_mib is not None and (type(memory_mib) is not int or not (0 <= memory_mib <= 10**9)):
        return _not_ready('invalid_snapshot')
    # No unknown fields pass through. Database migration is deliberately NOT
    # queried without a separate read-only DB identity and reviewed design.
    return {
        'available': True,
        'production_access': 'snapshot_only',
        'checked_at': stamp,
        'age_seconds': max(0, round(age)),
        'services': {k: services[k] for k in SERVICES},
        'production': {
            'git_commit': commit, 'git_branch': branch,
            'tracked_or_untracked_changes': changed,
            'health_http_status': http_status,
            'database_revision': 'not_checked',
        },
        'host': {'disk_available_gib': free_gib, 'memory_available_mib': memory_mib},
    }
