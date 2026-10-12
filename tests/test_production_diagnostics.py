"""Read-only snapshot contract; no subprocess is started by the MCP reader."""
import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bridge.production_status import read_snapshot
from scripts.production_snapshot import write_snapshot


def good_snapshot(now):
    return {
        'schema': 1,
        'checked_at': now.isoformat().replace('+00:00', 'Z'),
        'services': {name: 'active' for name in ('crew', 'nginx', 'docker', 'crew-tunnel')},
        'production': {
            'git_commit': 'a' * 40, 'git_branch': 'main',
            'tracked_or_untracked_changes': False, 'health_http_status': 200,
        },
        'host': {'disk_available_gib': 42.5, 'memory_available_mib': 8192},
    }


class TestProductionSnapshot(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.path = self.root / 'status.json'
        self.now = datetime.now(timezone.utc)

    def save(self, status):
        self.path.write_text(json.dumps(status), encoding='utf-8')

    def test_missing_does_not_crash_or_expose_paths(self):
        response = read_snapshot(self.path, now=self.now)
        self.assertFalse(response['available'])
        self.assertNotIn(str(self.path), str(response))

    def test_read_only_fresh_snapshot(self):
        self.save(good_snapshot(self.now))
        before = self.path.stat().st_mtime_ns
        out = read_snapshot(self.path, now=self.now)
        self.assertTrue(out['available'])
        self.assertEqual(out['production']['git_commit'], 'a' * 40)
        self.assertEqual(out['services']['crew'], 'active')
        self.assertEqual(out['production']['database_revision'], 'not_checked')
        self.assertEqual(before, self.path.stat().st_mtime_ns)
        self.assertEqual(out['production_access'], 'snapshot_only')

    def test_stale_snapshot_not_reported_as_current(self):
        old = self.now - timedelta(hours=1)
        self.save(good_snapshot(old))
        response = read_snapshot(self.path, now=self.now)
        self.assertFalse(response['available'])
        self.assertEqual(response['reason'], 'stale_snapshot')
        self.assertNotIn('services', response)

    def test_unexpected_fields_cannot_leak(self):
        data = good_snapshot(self.now)
        data['prod_secret'] = 'SECRET_DO_NOT_LEAK'
        data['production']['db_password'] = 'SECRET_DO_NOT_LEAK'
        data['host']['private_ip'] = 'SECRET_DO_NOT_LEAK'
        self.save(data)
        output = read_snapshot(self.path, now=self.now)
        self.assertTrue(output['available'])
        self.assertNotIn('SECRET_DO_NOT_LEAK', json.dumps(output))

    def test_invalid_fields_and_oversize_rejected(self):
        bad = good_snapshot(self.now)
        bad['services']['crew'] = ['active']
        self.save(bad)
        self.assertFalse(read_snapshot(self.path, now=self.now)['available'])
        self.path.write_text('X' * 5000, encoding='ascii')
        self.assertFalse(read_snapshot(self.path, now=self.now)['available'])

    def test_symlink_rejected(self):
        other = self.root / 'target'
        other.write_text(json.dumps(good_snapshot(self.now)))
        self.path.symlink_to(other)
        self.assertEqual(read_snapshot(self.path, now=self.now)['reason'], 'invalid_snapshot')

    def test_atomic_writer_for_directory_bind_mount(self):
        self.save({'old': True})
        old_inode = self.path.stat().st_ino
        write_snapshot(good_snapshot(self.now), self.root)
        self.assertNotEqual(old_inode, self.path.stat().st_ino)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)
        self.assertTrue(read_snapshot(self.path, now=self.now)['available'])
        self.assertFalse(any(x.name.startswith('.status-') for x in self.root.iterdir()))


if __name__ == '__main__':
    unittest.main()
