#!/usr/bin/env bash
# Reviewed, one-time, in-place REA vault upgrade. NOT callable through MCP.
set -euo pipefail
[[ "$EUID" == 0 ]] || { echo "Must run as root after merging the PR" >&2; exit 1; }
SRC=/opt/crew-dev-bridge
REA=/opt/rea-lite
AGENT=/usr/local/lib/crew-rea
UNIT=/etc/systemd/system/crew-rea-relay.service
BEFORE=96bd83cf96e145a13251c6f0ff633a607c15ba57

[[ "$(realpath "$(dirname "$0")/../..")" == "$SRC" && -d "$SRC/.git" ]] || {
  echo "Expected checked-out CREW bridge source" >&2; exit 1;
}
[[ "$(git -C "$SRC" branch --show-current)" == main ]] || {
  echo "Merge the reviewed PR into main first" >&2; exit 1;
}
[[ -z "$(git -C "$SRC" status --porcelain)" ]] || {
  echo "Bridge checkout has uncommitted changes; refusing" >&2; exit 1;
}
[[ -d "$REA" && ! -L "$REA" && -d "$AGENT" && ! -L "$AGENT" ]] || {
  echo "Expected existing isolated REA/relay installations" >&2; exit 1;
}
[[ -d "$REA/artifacts" && ! -L "$REA/artifacts" ]] || {
  echo "Existing REA artifacts directory missing" >&2; exit 1;
}
[[ ! -e "$REA/managed" && ! -L "$REA/managed" ]] || {
  echo "Managed vault already exists; use a separately reviewed upgrade" >&2; exit 1;
}
getent passwd nobody >/dev/null
getent group 986 >/dev/null
# Docker REA runs at 1000:1000. The vault is owned by nobody, group 1000;
# REA can read finalized files but cannot write them.
for spec in \
  "host/rea_relay.py:$AGENT/rea_relay.py" \
  "deploy/rea-bridge/crew-rea-relay.service:$UNIT" \
  "deploy/rea-lite/compose.yaml:$REA/compose.yaml"; do
  IFS=: read -r rel installed <<< "$spec"
  [[ -f "$installed" && ! -L "$installed" && ! -e "$installed.before-vault" ]] || {
    echo "Unexpected/missing original: $installed" >&2; exit 1;
  }
  git -C "$SRC" show "$BEFORE:$rel" | cmp -s - "$installed" || {
    echo "Existing $installed differs from reviewed PR #25; refusing" >&2; exit 1;
  }
done
[[ ! -e "$AGENT/rea_store.py" && ! -L "$AGENT/rea_store.py" ]] || {
  echo "Vault implementation already installed" >&2; exit 1;
}
for rel in host/rea_store.py host/rea_relay.py deploy/rea-bridge/crew-rea-relay.service deploy/rea-lite/compose.yaml; do
  [[ -f "$SRC/$rel" && ! -L "$SRC/$rel" ]] || {
    echo "Missing or symlinked reviewed source: $rel" >&2; exit 1;
  }
done

# Backup only the changed files; no evidence or user data is removed.
for installed in "$AGENT/rea_relay.py" "$UNIT" "$REA/compose.yaml"; do
  install -o root -g root -m 0644 "$installed" "$installed.before-vault"
done

install -d -o nobody -g 1000 -m 2750 "$REA/managed"
install -d -o nobody -g 1000 -m 2700 "$REA/managed/.uploads"
# Setgid inheritance ensures completed MCP uploads remain readable by
# the REA container's fixed GID 1000 after atomic publication.
chmod 2750 "$REA/managed"
chmod 2700 "$REA/managed/.uploads"
install -o root -g root -m 0644 "$SRC/host/rea_store.py" "$AGENT/rea_store.py"
install -o root -g root -m 0644 "$SRC/host/rea_relay.py" "$AGENT/rea_relay.py"
install -o root -g root -m 0644 "$SRC/deploy/rea-bridge/crew-rea-relay.service" "$UNIT"
install -o root -g root -m 0644 "$SRC/deploy/rea-lite/compose.yaml" "$REA/compose.yaml"

# Read-only REA vault mount; preserve existing evidence and legacy artifacts.
cd "$REA"
docker compose config --quiet
docker compose up -d --no-deps --force-recreate rea
docker compose restart ingress
python3 scripts/smoke_test.py

systemctl daemon-reload
systemctl restart crew-rea-relay.service
systemctl is-active --quiet crew-rea-relay.service || {
  echo "REA relay not active; inspect journalctl -u crew-rea-relay.service" >&2; exit 1;
}
for i in 1 2 3 4 5; do
  if [[ -S /run/crew-rea/rea.sock ]]; then break; fi
  sleep 1
done
[[ -S /run/crew-rea/rea.sock ]] || {
  echo "REA relay Unix socket is missing" >&2; exit 1;
}
cd "$SRC"
docker compose config --quiet
docker compose up -d --build --force-recreate --no-deps bridge

echo "PASS: Dedicated REA vault installed at $REA/managed"
echo "ChatGPT: refresh the existing CREW connector and call rea_vault_list."
echo "No second tunnel, public port, CREW game or wedding restart was required."
