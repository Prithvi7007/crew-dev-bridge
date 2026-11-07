#!/usr/bin/env bash
# Reviewed code-only REA Web Intelligence upgrade.
# Never removes evidence volumes, vault data, or restarts unrelated apps.
set -euo pipefail
(( EUID == 0 )) || { echo "Use sudo after merge and code review" >&2; exit 1; }

SRC=/opt/crew-dev-bridge
LAB=/opt/rea-lab
RELAY=/usr/local/lib/crew-rea/rea_relay.py

[[ -d "$SRC/.git" && ! -L "$SRC" ]] || { echo "Expected bridge repo" >&2; exit 1; }
[[ "$(git -C "$SRC" branch --show-current)" == main ]] || { echo "Requires merged main" >&2; exit 1; }
[[ -z "$(git -C "$SRC" status --porcelain)" ]] || { echo "Dirty source checkout" >&2; exit 1; }
[[ "$(git -C "$SRC" remote get-url origin)" =~ ^(https://github.com/Prithvi7007/crew-dev-bridge(\.git)?|git@github\.com:Prithvi7007/crew-dev-bridge\.git)$ ]] || {
    echo "Unexpected source remote" >&2; exit 1;
}
# Existing integrated Android lab and private REA service must be present.
for p in "$LAB/api.py" "$LAB/intelligence.py" "$LAB/dashboard.html" "$LAB/Dockerfile" \
         "$LAB/compose.yaml" "$LAB/.token" "$RELAY"; do
    [[ -f "$p" && ! -L "$p" ]] || { echo "Missing/untrusted live file: $p" >&2; exit 1; }
done
for p in api.py intelligence.py web_intelligence.py web_capture.py dashboard.html \
         Dockerfile WebCapture.Dockerfile compose.yaml; do
    [[ -f "$SRC/deploy/rea-lab/$p" && ! -L "$SRC/deploy/rea-lab/$p" ]] || {
        echo "Missing new source: $p" >&2; exit 1;
    }
done
grep -q 'def rea_web_submit' "$SRC/bridge/server.py"
grep -q 'web_intelligence' "$SRC/deploy/rea-lab/api.py"

backup="$LAB/backups/web-$(date -u +%Y%m%dT%H%M%SZ)"
[[ ! -e "$backup" ]] || { echo "Backup name already exists" >&2; exit 1; }
install -d -o root -g root -m 0700 "$backup"
for p in api.py intelligence.py web_intelligence.py web_capture.py dashboard.html \
         Dockerfile WebCapture.Dockerfile compose.yaml; do
    if [[ -f "$LAB/$p" ]]; then cp -p "$LAB/$p" "$backup/$p"; fi
done
cp -p "$RELAY" "$backup/rea_relay.py"

for p in api.py intelligence.py web_intelligence.py web_capture.py dashboard.html \
         Dockerfile WebCapture.Dockerfile compose.yaml; do
    install -o root -g root -m 0644 "$SRC/deploy/rea-lab/$p" "$LAB/$p"
done

# Keep existing named lab_evidence and lab_workspace volumes untouched.
cd "$LAB"
docker compose config --quiet
docker compose up -d --build --force-recreate --no-deps web_capture lab
docker compose ps web_capture lab ingress

# Test internal-only access to sidecar, then authenticated worker readiness.
docker compose exec -T lab python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen("http://web_capture:8767/health", timeout=8) as r:
    assert json.load(r).get("ok") is True, "capture_sidecar_not_healthy"
print("Web capture sidecar reachable only on private Docker networks")
PY
python3 - "$LAB/.token" <<'PY'
import json, pathlib, sys, time, urllib.request
token = pathlib.Path(sys.argv[1]).read_text(encoding="ascii").strip()
for _ in range(25):
    try:
        request = urllib.request.Request(
            "http://127.0.0.1:18766/capabilities",
            headers={"X-REA-LAB-TOKEN": token})
        with urllib.request.urlopen(request, timeout=4) as response:
            data = json.load(response)
        if data.get("ok") and data.get("web_intelligence") and data.get("source_index"):
            print("REA Web Intelligence API healthy")
            break
    except (OSError, ValueError):
        time.sleep(2)
else:
    raise SystemExit("Worker did not expose web intelligence; inspect REA containers")
PY

# Refresh only REA host relay and the MCP bridge adapter, not CREW Games or wedding.
install -o root -g root -m 0644 "$SRC/host/rea_relay.py" "$RELAY"
systemctl restart crew-rea-relay.service
systemctl is-active --quiet crew-rea-relay.service
cd "$SRC"
docker compose config --quiet
docker compose up -d --build --force-recreate --no-deps bridge
echo "REA Web Intelligence deployed. Existing APK evidence volumes preserved."
echo "Backup: $backup"
echo "Refresh CREW MCP connection for rea_web_submit and rea_web_compare."
echo "Use the existing private dashboard SSH tunnel on localhost port 18766."
