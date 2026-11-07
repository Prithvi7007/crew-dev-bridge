#!/usr/bin/env bash
# One-time, operator-reviewed, narrowly scoped REA Lab installation.
set -euo pipefail
[[ "$EUID" == 0 ]] || { echo "Run as root after merging PR" >&2; exit 1; }
SRC=/opt/crew-dev-bridge
DEST=/opt/rea-lab
AGENT=/usr/local/lib/crew-rea
UNIT=crew-rea-relay.service
[[ "$(realpath "$(dirname "$0")/../..")" == "$SRC" ]] || exit 1
[[ -d "$SRC/.git" && "$(git -C "$SRC" branch --show-current)" == main ]] || {
  echo "Use a reviewed clean checkout of main" >&2; exit 1;
}
[[ -z "$(git -C "$SRC" status --porcelain)" ]] || { echo "Dirty checkout" >&2; exit 1; }
[[ -d /opt/rea-lite/managed && ! -L /opt/rea-lite/managed ]] || exit 1
[[ -f "$AGENT/rea_relay.py" && ! -L "$AGENT/rea_relay.py" ]] || exit 1
[[ ! -e "$DEST" && ! -L "$DEST" ]] || { echo "REA Lab already installed" >&2; exit 1; }
[[ -f "$SRC/deploy/rea-lab/Dockerfile" ]] || exit 1
[[ "$(df --output=avail -B1 /opt | tail -1 | tr -d ' ')" -ge 10737418240 ]] || {
  echo "Need 10 GiB free for first install" >&2; exit 1;
}
python3 - <<'PY'
import socket
s = socket.socket()
try:
    s.bind(("127.0.0.1", 18766))
finally:
    s.close()
PY
install -d -o root -g root -m 0755 "$DEST"
for file in Dockerfile compose.yaml nginx.conf api.py; do
  install -o root -g root -m 0644 "$SRC/deploy/rea-lab/$file" "$DEST/$file"
done
install -o nobody -g root -m 0400 /dev/null "$DEST/.token"
python3 - "$DEST/.token" <<'PY'
import secrets, sys
with open(sys.argv[1], 'w', encoding='ascii') as f:
    f.write(secrets.token_hex(32))
PY
cd "$DEST"
docker compose config --quiet
docker compose up -d --build
python3 - "$DEST/.token" <<'PY'
import json, pathlib, sys, time, urllib.request
token = pathlib.Path(sys.argv[1]).read_text()
for _ in range(25):
    try:
        request = urllib.request.Request("http://127.0.0.1:18766/capabilities",
            headers={"X-REA-LAB-TOKEN": token})
        with urllib.request.urlopen(request, timeout=3) as response:
            result = json.load(response)
        if result.get("ok") and result.get("jadx"):
            print("REA Lab verified: static JADX worker ready")
            break
    except (OSError, ValueError):
        time.sleep(2)
else:
    raise SystemExit("REA Lab failed to pass authenticated health check")
PY
# Host relay change is deliberately a separate, narrow deployment boundary.
# Back up the original; do not alter artifacts, REA MCP, games or wedding.
backup="$AGENT/rea_relay.py.before-lab"
[[ ! -e "$backup" ]] || { echo "Relay backup exists; manual review needed" >&2; exit 1; }
install -o root -g root -m 0644 "$AGENT/rea_relay.py" "$backup"
install -o root -g root -m 0644 "$SRC/host/rea_relay.py" "$AGENT/rea_relay.py"
systemctl restart "$UNIT"
systemctl is-active --quiet "$UNIT"
cd "$SRC"
docker compose config --quiet
docker compose up -d --build --force-recreate --no-deps bridge
echo "REA Lab installed. Refresh CREW connector and call rea_lab_capabilities."
echo "No other application services or data volumes were modified."
