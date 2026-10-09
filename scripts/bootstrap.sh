#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  echo 'Docker with Compose v2 is required. Stop: nothing installed.' >&2
  exit 1
fi
if [ -e source ] && [ ! -d source/.git ]; then
  echo 'source/ exists but is not a Git checkout; refusing to overwrite.' >&2
  exit 1
fi
mkdir -p queue/{jobs,results,snapshots} secrets audit
if [ ! -f secrets/github_token ]; then
  echo 'Create a fine-grained GitHub token scoped ONLY to Prithvi7007/crew_games.'
  echo 'Repository permissions: Contents read/write; Pull requests read/write; Metadata read.'
  read -r -s -p 'Paste token (input hidden): ' token; echo
  if [ -z "$token" ]; then echo 'Missing token' >&2; exit 1; fi
  (umask 077; printf '%s\n' "$token" > secrets/github_token)
  unset token
fi
if [ ! -d source/.git ]; then
  git clone --no-tags --branch main https://github.com/Prithvi7007/crew_games.git source
fi
# The runner image is built ONLY at setup/rebuild; it does not have production mounts.
# Use a temporary combined Docker build context for the pinned repo dependencies and worker.
mkdir -p .runner-context
cp source/requirements.txt .runner-context/requirements.txt
mkdir -p .runner-context/frontend .runner-context/runner
cp source/frontend/package.json source/frontend/package-lock.json .runner-context/frontend/
cp runner/worker.py .runner-context/runner/
docker build --pull -t crew-dev-runner:local -f runner/Dockerfile .runner-context
rm -rf .runner-context
chown -R 10001:10001 source queue secrets audit
chmod 0700 secrets audit
# The bridge (10001:10001) and runner supervisor (0:10001) share
# a group-private queue; test subprocesses (10002:10002) cannot enter.
chmod 2770 queue queue/jobs queue/results queue/snapshots
chmod -R g+rwX queue
chmod 0600 secrets/github_token
docker compose up -d --build
printf '\nLocal MCP endpoint: http://127.0.0.1:8777/mcp\n'
printf 'NEXT: configure a Secure MCP Tunnel; do not publicly expose this port.\n'
