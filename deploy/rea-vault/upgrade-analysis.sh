#!/usr/bin/env bash
# Operator-only upgrade of the existing REA vault relay and MCP bridge.
set -Eeuo pipefail
[[ "$EUID" == 0 ]] || { echo "Run as root after reviewed merge" >&2; exit 1; }
SRC=/opt/crew-dev-bridge
REA=/opt/rea-lite
AGENT=/usr/local/lib/crew-rea
UNIT=/etc/systemd/system/crew-rea-relay.service
BEFORE=c8e5b689cde464a076aacbf036b8a196d6f71f00
BACKUP="$AGENT/rea_relay.py.before-analysis-views"
[[ "$(realpath "$(dirname "$0")/../..")" == "$SRC" && -d "$SRC/.git" ]]
[[ "$(git -C "$SRC" branch --show-current)" == main ]] || {
  echo "Merge the reviewed PR into main first" >&2; exit 1;
}
[[ -z "$(git -C "$SRC" status --porcelain)" ]] || {
  echo "Uncommitted source changes; refusing" >&2; exit 1;
}
for directory in "$REA" "$REA/managed" "$AGENT"; do
  [[ -d "$directory" && ! -L "$directory" ]] || exit 1
done
[[ ! -e "$BACKUP" && ! -L "$BACKUP" ]] || {
  echo "Existing recovery copy; refusing to overwrite" >&2; exit 1;
}
for spec in "host/rea_relay.py:$AGENT/rea_relay.py" \
            "host/rea_store.py:$AGENT/rea_store.py" \
            "deploy/rea-bridge/crew-rea-relay.service:$UNIT"; do
  IFS=: read -r rel installed <<< "$spec"
  [[ -f "$installed" && ! -L "$installed" ]] || exit 1
  git -C "$SRC" show "$BEFORE:$rel" | cmp -s - "$installed" || {
    echo "Installed file differs from reviewed vault baseline: $installed" >&2; exit 1;
  }
done
[[ -f "$SRC/host/rea_relay.py" && ! -L "$SRC/host/rea_relay.py" ]] || exit 1
cd "$SRC"
docker compose config --quiet
# Build before touching the running relay.
docker compose build bridge
install -o root -g root -m 0644 "$AGENT/rea_relay.py" "$BACKUP"
wait_for_socket() {
  for i in 1 2 3 4 5; do
    if [[ -S /run/crew-rea/rea.sock ]]; then return 0; fi
    sleep 1
  done
  [[ -S /run/crew-rea/rea.sock ]]
}
rollback() {
  code=$?
  trap - ERR
  echo "Upgrade failed; restoring original relay from $BACKUP" >&2
  if install -o root -g root -m 0644 "$BACKUP" "$AGENT/rea_relay.py" &&
     systemctl restart crew-rea-relay.service &&
     systemctl is-active --quiet crew-rea-relay.service && wait_for_socket; then
    # The relay recreates its socket; refresh the bridge's bind mount too.
    docker compose up -d --no-deps --force-recreate bridge ||
      echo "ERROR: Relay restored but bridge recreation failed; operator recovery required" >&2
  else
    echo "ERROR: Relay recovery incomplete; operator recovery required using $BACKUP" >&2
  fi
  exit "$code"
}
trap rollback ERR
install -o root -g root -m 0644 "$SRC/host/rea_relay.py" "$AGENT/rea_relay.py"
systemctl restart crew-rea-relay.service
systemctl is-active --quiet crew-rea-relay.service
wait_for_socket
docker compose up -d --no-deps --force-recreate bridge
docker compose exec -T bridge python - <<'PY'
from bridge.rea_gateway import call
result = call('capabilities')
assert result.get('ok'), result
assert {'vault_entries', 'vault_evidence'} <= set(result.get('operations', [])), result
assert result.get('max_view_bytes') == 12000, result
assert call('status').get('ok'), 'REA session check failed'
print('Relay capabilities and REA session verified')
PY
trap - ERR
echo "PASS: REA bounded evidence views installed; recovery copy retained at $BACKUP"
echo "Refresh CREW connector actions, then verify the vault SHA and request entries."
