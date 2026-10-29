#!/usr/bin/env bash
# Read-only host preflight for the dedicated REA Lite Compose project.
set -euo pipefail
cd "$(dirname "$0")/.."
for command in docker python3; do
  command -v "$command" >/dev/null || { echo "Missing prerequisite: $command" >&2; exit 1; }
done
docker compose version >/dev/null
docker info >/dev/null
docker compose config --quiet

docker_version="$(docker version --format '{{.Server.Version}}')"
python3 - "$docker_version" <<'PYVERSION'
import sys
major = sys.argv[1].split('.', 1)[0]
if not major.isdigit() or int(major) < 28:
    raise SystemExit("Docker Engine >=28 required for localhost-only port safety")
PYVERSION

python3 - <<'PY'
import shutil
import socket
import sys
from pathlib import Path
with Path('/proc/meminfo').open(encoding='ascii') as stream:
    mem = dict((line.split(':', 1)[0], int(line.split(':', 1)[1].strip().split()[0]))
               for line in stream if ':' in line)
available_mib = mem.get('MemAvailable', 0) // 1024
free_gib = shutil.disk_usage('.').free / 1024**3
if available_mib < 1600 or free_gib < 4:
    raise SystemExit('Insufficient headroom; require >=1600 MiB RAM and >=4 GiB free disk')
artifacts = Path('artifacts')
if not artifacts.is_dir() or artifacts.is_symlink():
    raise SystemExit('Expected ordinary artifacts directory (not a symlink)')
with socket.socket() as sock:
    try:
        sock.bind(('127.0.0.1', 18765))
    except OSError as exc:
        raise SystemExit('Port 127.0.0.1:18765 unavailable; do not modify existing services') from exc
print(f'PASS: available {available_mib} MiB RAM, {free_gib:.1f} GiB disk; private port free')
PY
