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
# Match systemd StateDirectory=crew-release's User=root and Group=986.
# A mismatch makes systemd recursively chown nested worktree directories on restart.
install -d -o root -g 986 -m 0755 /var/lib/crew-release
install -d -o crew -g www-data -m 0750 /var/lib/crew-release/worktrees
# Git worktree add must register metadata as unprivileged crew. Only repair the
# fixed Git registry directory; never recursively chown production checkout.
[[ -d /opt/crew/.git && ! -L /opt/crew/.git ]] || {
  echo "Expected regular production Git metadata directory" >&2; exit 1;
}
[[ ! -L /opt/crew/.git/worktrees ]] || {
  echo "Refusing symlinked Git worktree registry" >&2; exit 1;
}
install -d -o crew -g www-data -m 0750 /opt/crew/.git/worktrees
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
# Verify after systemd has applied StateDirectory ownership changes.
[[ "$(stat -c '%u:%g' /var/lib/crew-release)" == "0:986" ]] || {
  echo "Release state directory ownership mismatches systemd service" >&2; exit 1;
}
for stage_dir in /var/lib/crew-release/worktrees /opt/crew/.git/worktrees; do
  [[ -d "${stage_dir}" && ! -L "${stage_dir}" ]] || {
    echo "Unexpected release staging directory" >&2; exit 1;
  }
  /usr/sbin/runuser -u crew -- /usr/bin/test -w "${stage_dir}" || {
    echo "CREW user cannot write release staging directory" >&2; exit 1;
  }
  /usr/sbin/runuser -u crew -- /usr/bin/test -x "${stage_dir}" || {
    echo "CREW user cannot traverse release staging directory" >&2; exit 1;
  }
done
echo "CREW reusable release agent installed. Check crew_release_capabilities and crew_release_plan."
