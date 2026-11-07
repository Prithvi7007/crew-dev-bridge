#!/usr/bin/env bash
# Resume the first REA Lab installation after Docker rejected the 1.5 CPU cap.
# An explicit operator-only recovery, not a rerun of install.sh.
set -euo pipefail

SRC=/opt/crew-dev-bridge
DEST=/opt/rea-lab
AGENT=/usr/local/lib/crew-rea
UNIT=crew-rea-relay.service
# Git SHA of reviewed PR #30, which created the original half-installed files.
BASE=4f1ed8ec80e89cc6162e1a707dee7006622c2e65

[[ "$EUID" == 0 ]] || { echo "Run as root after review/merge" >&2; exit 1; }
[[ "$(realpath "$(dirname "$0")/../..")" == "$SRC" ]] || {
  echo "Unexpected script checkout" >&2; exit 1;
}
[[ -d "$SRC/.git" && "$(git -C "$SRC" branch --show-current)" == main ]] || {
  echo "Expected checked-out main branch" >&2; exit 1;
}
[[ -z "$(git -C "$SRC" status --porcelain)" ]] || {
  echo "Dirty checkout; preserve local changes" >&2; exit 1;
}
[[ -d "$DEST" && ! -L "$DEST" && -d /opt/rea-lite/managed &&
    ! -L /opt/rea-lite/managed ]] || {
  echo "Expected existing partial REA Lab installation and REA vault" >&2; exit 1;
}
[[ -d "$AGENT" && ! -L "$AGENT" &&
    -f "$AGENT/rea_relay.py" && ! -L "$AGENT/rea_relay.py" ]] || {
  echo "Existing REA relay missing" >&2; exit 1;
}
[[ ! -e "$AGENT/rea_relay.py.before-lab" ]] || {
  echo "Relay has already been upgraded or a recovery was attempted" >&2; exit 1;
}
[[ -f "$DEST/.token" && ! -L "$DEST/.token" &&
    "$(stat -c '%U:%a' "$DEST/.token")" == "nobody:400" ]] || {
  echo "Missing or unexpected REA Lab authentication token; refusing" >&2; exit 1;
}
[[ ! -e "$DEST/compose.yaml.before-one-cpu" ]] || {
  echo "CPU recovery has already been attempted" >&2; exit 1;
}

# Confirm that this is *exactly* PR #30's paused installation.
# Preserve existing tokens, vault artifacts, and all evidence volumes.
for file in compose.yaml Dockerfile nginx.conf api.py; do
  [[ -f "$DEST/$file" && ! -L "$DEST/$file" ]] || {
    echo "Missing installed file: $file" >&2; exit 1;
  }
  git -C "$SRC" show "$BASE:deploy/rea-lab/$file" | cmp -s - "$DEST/$file" || {
    echo "Installed $file differs from reviewed baseline; stop for inspection" >&2; exit 1;
  }
done
[[ -f "$SRC/deploy/rea-lab/compose.yaml" &&
    ! -L "$SRC/deploy/rea-lab/compose.yaml" ]] || exit 1

# Scope changes to REA Lab; no Docker rebuild required after the prior build.
docker image inspect rea-lab:jadx-1.5.6 >/dev/null
install -o root -g root -m 0644 "$DEST/compose.yaml" "$DEST/compose.yaml.before-one-cpu"
install -o root -g root -m 0644 "$SRC/deploy/rea-lab/compose.yaml" "$DEST/compose.yaml"
cd "$DEST"
docker compose config --quiet
docker compose up -d --no-build

python3 - "$DEST/.token" <<'PY'
import json, pathlib, sys, time, urllib.request
token = pathlib.Path(sys.argv[1]).read_text(encoding="ascii").strip()
for attempt in range(25):
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:18766/capabilities",
            headers={"X-REA-LAB-TOKEN": token},
        )
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.load(response)
        if data.get("ok") and data.get("jadx"):
            print("PASS: REA Lab authenticated JADX service ready")
            break
    except (OSError, ValueError):
        time.sleep(2)
else:
    raise SystemExit("REA Lab container did not pass authenticated readiness")
PY

# Upgrade only the existing CREW REA relay and MCP bridge after readiness.
# Ensure existing relay has not already installed the new LAB operations.
if grep -q '"lab_capabilities"' "$AGENT/rea_relay.py"; then
  echo "Relay already includes lab operations; manual review required" >&2
  exit 1
fi
install -o root -g root -m 0644 "$AGENT/rea_relay.py" "$AGENT/rea_relay.py.before-lab"
install -o root -g root -m 0644 "$SRC/host/rea_relay.py" "$AGENT/rea_relay.py"
systemctl restart "$UNIT"
systemctl is-active --quiet "$UNIT"
cd "$SRC"
docker compose config --quiet
docker compose up -d --build --force-recreate --no-deps bridge

echo "PASS: REA Lab deployed on one-vCPU VPS; existing vault and evidence retained"
echo "Refresh the CREW ChatGPT connector; call rea_lab_capabilities."
echo "No CREW Games, wedding, or REA Lite service was restarted."
