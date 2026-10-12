#!/usr/bin/env bash
# One-time, deliberate operator installation. Review this file before running it.
set -euo pipefail
[[ "${EUID}" == 0 ]] || { echo "Run as root after review" >&2; exit 1; }
REPO=/opt/crew-dev-bridge
[[ -d "${REPO}/.git" ]] || { echo "Missing bridge checkout" >&2; exit 1; }
[[ "$(git -C "${REPO}" symbolic-ref --short HEAD)" == main ]] || {
  echo "Install only from reviewed, merged bridge main" >&2; exit 1;
}
[[ -z "$(git -C "${REPO}" status --porcelain)" ]] || {
  echo "Bridge checkout must be clean" >&2; exit 1;
}
[[ "$(id -u crew)" =~ ^[0-9]+$ ]] || exit 1
getent group 986 >/dev/null || { echo "Expected bridge socket group 986" >&2; exit 1; }
install -d -o root -g root -m 0755 /usr/local/lib/crew-release
install -o root -g root -m 0644 "${REPO}/host/release_agent.py" /usr/local/lib/crew-release/release_agent.py
install -o root -g root -m 0644 "${REPO}/deploy/release/crew-release.service" /etc/systemd/system/crew-release.service
install -d -o root -g root -m 0755 /var/lib/crew-release
install -d -o crew -g www-data -m 0750 /var/lib/crew-release/worktrees
install -d -o root -g postgres -m 0750 /var/backups/crew
systemctl daemon-reload
systemctl enable crew-release.service
# enable --now leaves an already-running worker on the previous code revision.
systemctl restart crew-release.service
# Recreate only the MCP bridge after the worker's socket directory is replaced.
# Otherwise Docker may keep a bind mount to the previous socket directory.
cd "${REPO}"
docker compose up -d --build --force-recreate --no-deps bridge
systemctl is-active --quiet crew-release.service
echo "CREW reusable release agent installed. Check crew_release_capabilities and crew_release_plan."
