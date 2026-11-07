#!/usr/bin/env bash
# One-time administrative bootstrap for the opt-in, root-capable VPS terminal.
# REVIEW THIS SERVICE: after setup the private MCP bridge can run ANY shell command
# as root through a prepare/execute workflow. Do not install on an untrusted bridge.
set -euo pipefail

(( EUID == 0 )) || { echo "Requires an administrator (sudo)" >&2; exit 1; }
repo=/opt/crew-dev-bridge
agent=/usr/local/lib/crew-vps-terminal/vps_terminal.py
unit=/etc/systemd/system/crew-vps-terminal.service

[[ -d "$repo/.git" && ! -L "$repo" ]] || { echo "Repository missing/unsafe" >&2; exit 1; }
[[ "$(git -C "$repo" branch --show-current)" == main ]] || {
    echo "Use reviewed merged main" >&2; exit 1;
}
[[ -z "$(git -C "$repo" status --porcelain)" ]] || {
    echo "Repository has local changes; stop for review" >&2; exit 1;
}
[[ "$(git -C "$repo" remote get-url origin)" =~ ^(https://github.com/Prithvi7007/crew-dev-bridge(\.git)?|git@github\.com:Prithvi7007/crew-dev-bridge\.git)$ ]] || {
    echo "Unexpected git remote" >&2; exit 1;
}
[[ "$(id -u crew)" =~ ^[0-9]+$ ]] || { echo "Missing crew host identity" >&2; exit 1; }
getent group 986 >/dev/null || { echo "Expected bridge socket GID 986" >&2; exit 1; }
[[ -f "$repo/host/vps_terminal.py" && -f "$repo/deploy/vps-terminal/crew-vps-terminal.service" ]] || exit 1
[[ ! -L "$unit" && ! -L "$agent" ]] || { echo "Refusing symlinked install target" >&2; exit 1; }

echo "Installing root-capable VPS terminal; this grants CREW MCP arbitrary host command execution."
install -d -o root -g root -m 0755 /usr/local/lib/crew-vps-terminal
install -o root -g root -m 0644 "$repo/host/vps_terminal.py" "$agent"
install -o root -g root -m 0644 "$repo/deploy/vps-terminal/crew-vps-terminal.service" "$unit"

systemctl daemon-reload
systemctl enable crew-vps-terminal.service
systemctl restart crew-vps-terminal.service
systemctl is-active --quiet crew-vps-terminal.service

# Check that the socket is private; don't print its contents or any secret.
for n in $(seq 1 20); do
    if [[ -S /run/crew-vps-terminal/terminal.sock ]]; then break; fi
    sleep 0.25
done
[[ -S /run/crew-vps-terminal/terminal.sock ]] || { echo "No terminal socket" >&2; exit 1; }
[[ "$(stat -c '%u:%g:%a' /run/crew-vps-terminal/terminal.sock)" == "0:986:660" ]] || {
    echo "Unsafe terminal socket ownership/mode" >&2; exit 1;
}

# Recreate only the bridge, NOT the game, wedding, runner, or REA workers.
cd "$repo"
docker compose config --quiet
docker compose up -d --build --force-recreate --no-deps bridge

echo "CREW general VPS terminal installed. Refresh the CREW connector."
echo "Usage: crew_vps_terminal_capabilities -> crew_vps_prepare -> explicit user approval -> crew_vps_execute -> crew_vps_job_status."
echo "This grants root host access through the private MCP socket. Keep the bridge and tunnel private."
