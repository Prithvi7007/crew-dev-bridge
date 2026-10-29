#!/usr/bin/env bash
# One-time, operator-reviewed bridge-to-REA installation. NO arbitrary shell MCP.
set -euo pipefail
[[ "$EUID" == 0 ]] || { echo "Run as root only after review" >&2; exit 1; }
SRC=/opt/crew-dev-bridge
[[ "$(realpath "$(dirname "$0")/../..")" == "$SRC" && -d "$SRC/.git" ]] || {
  echo "Expected /opt/crew-dev-bridge checkout" >&2; exit 1;
}
[[ "$(git -C "$SRC" branch --show-current)" == main ]] || {
  echo "Requires reviewed and merged main" >&2; exit 1;
}
[[ -z "$(git -C "$SRC" status --porcelain)" ]] || {
  echo "Bridge checkout must be clean" >&2; exit 1;
}
getent passwd nobody >/dev/null || { echo "Missing nobody user" >&2; exit 1; }
getent group 986 >/dev/null || { echo "Missing bridge socket group 986" >&2; exit 1; }
[[ -d /opt/rea-lite/artifacts && ! -L /opt/rea-lite/artifacts ]] || {
  echo "REA artifact upload directory missing or symlinked" >&2; exit 1;
}
[[ -f /opt/rea-lite/scripts/smoke_test.py ]] || {
  echo "REA Lite smoke test missing" >&2; exit 1;
}
python3 /opt/rea-lite/scripts/smoke_test.py || {
  echo "REA MCP is not healthy; refusing relay installation" >&2; exit 1;
}
[[ ! -e /usr/local/lib/crew-rea && ! -L /usr/local/lib/crew-rea ]] || {
  echo "Dedicated REA relay installation already exists; refusing overwrite" >&2; exit 1;
}
[[ ! -e /etc/systemd/system/crew-rea-relay.service ]] || {
  echo "REA relay unit already installed; refusing overwrite" >&2; exit 1;
}
[[ ! -e /run/crew-rea && ! -L /run/crew-rea ]] || {
  echo "Unexpected existing REA relay socket directory" >&2; exit 1;
}

install -d -o root -g root -m 0755 /usr/local/lib/crew-rea
install -o root -g root -m 0644 "$SRC/host/rea_relay.py" /usr/local/lib/crew-rea/rea_relay.py
install -o root -g root -m 0644 "$SRC/deploy/rea-bridge/crew-rea-relay.service" /etc/systemd/system/crew-rea-relay.service
systemctl daemon-reload
systemctl enable --now crew-rea-relay.service
systemctl is-active --quiet crew-rea-relay.service || {
  echo "REA relay failed to start: inspect systemctl status crew-rea-relay" >&2; exit 1;
}
[[ -S /run/crew-rea/rea.sock ]] || {
  echo "REA relay socket missing; inspect journalctl -u crew-rea-relay" >&2; exit 1;
}
# Recreate ONLY the development MCP bridge so its new socket mount and fixed
# tools become visible on the existing CREW secure tunnel. Do not touch runner,
# production CREW, wedding, or their release agents.
cd "$SRC"
docker compose config --quiet
docker compose up -d --build --force-recreate --no-deps bridge
echo "REA scoped tools installed on existing CREW MCP tunnel."
echo "Check rea_bridge_capabilities and rea_bridge_status in ChatGPT."
