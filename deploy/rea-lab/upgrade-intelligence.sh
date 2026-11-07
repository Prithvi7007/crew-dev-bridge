#!/usr/bin/env bash
# Reviewed, one-release REA Intelligence Lab upgrade. Never operates on evidence volumes.
set -euo pipefail
(( EUID == 0 )) || { echo "Run using sudo after PR merge" >&2; exit 1; }

SRC=/opt/crew-dev-bridge
LAB=/opt/rea-lab
RELAY=/usr/local/lib/crew-rea/rea_relay.py
[[ -d "$SRC/.git" ]] || { echo "Missing source checkout" >&2; exit 1; }
[[ "$(git -C "$SRC" branch --show-current)" == main ]] || {
  echo "Requires reviewed main checkout" >&2; exit 1;
}
[[ -z "$(git -C "$SRC" status --porcelain)" ]] || {
  echo "Source checkout is not clean" >&2; exit 1;
}
for p in "$LAB/api.py" "$LAB/Dockerfile" "$LAB/compose.yaml" "$LAB/.token" "$RELAY"; do
  [[ -f "$p" && ! -L "$p" ]] || { echo "Missing/untrusted REA file: $p" >&2; exit 1; }
done
for p in api.py intelligence.py dashboard.html Dockerfile; do
  [[ -f "$SRC/deploy/rea-lab/$p" ]] || exit 1
done
grep -q 'def rea_lab_investigate' "$SRC/bridge/server.py"
grep -q 'def index_state' "$SRC/deploy/rea-lab/api.py"

backup="$LAB/backups/integrated-$(date -u +%Y%m%dT%H%M%SZ)"
[[ ! -e "$backup" ]] || { echo "Backup already exists" >&2; exit 1; }
install -d -o root -g root -m 0700 "$backup"
for p in api.py Dockerfile; do cp -p "$LAB/$p" "$backup/$p"; done
[[ ! -f "$LAB/intelligence.py" ]] || cp -p "$LAB/intelligence.py" "$backup/intelligence.py"
[[ ! -f "$LAB/dashboard.html" ]] || cp -p "$LAB/dashboard.html" "$backup/dashboard.html"
cp -p "$RELAY" "$backup/rea_relay.py"

for p in api.py intelligence.py dashboard.html Dockerfile; do
  install -o root -g root -m 0644 "$SRC/deploy/rea-lab/$p" "$LAB/$p"
done

# Recreate only the worker; DO NOT stop/remove evidence or workspace volumes.
cd "$LAB"
docker compose config --quiet
docker compose up -d --build --force-recreate --no-deps lab
docker compose ps lab

# Authenticated health check, without printing the secret.
python3 - "$LAB/.token" <<'PY'
import json, pathlib, sys, time, urllib.request
token = pathlib.Path(sys.argv[1]).read_text(encoding="ascii").strip()
for _ in range(20):
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:18766/capabilities",
            headers={"X-REA-LAB-TOKEN": token})
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.load(response)
        if data.get("ok") and data.get("source_index") and data.get("dashboard"):
            print("REA intelligence worker API healthy")
            break
    except (OSError, ValueError):
        time.sleep(2)
else:
    raise SystemExit("REA worker not ready: inspect lab container before proceeding")
PY

# Upgrade ONLY the dedicated REA relay and CREW MCP bridge adapter.
install -o root -g root -m 0644 "$SRC/host/rea_relay.py" "$RELAY"
systemctl restart crew-rea-relay.service
systemctl is-active --quiet crew-rea-relay.service
cd "$SRC"
docker compose config --quiet
docker compose up -d --build --force-recreate --no-deps bridge

echo "Integrated REA Lab deployed; no REA volumes were removed."
echo "Backup: $backup"
echo "Other app containers (CREW Games, wedding) were not restarted."
echo "Refresh the CREW connector to reveal the new investigation tools."
echo "For the dashboard, SSH-forward local port 18766; never paste the token into chat."
