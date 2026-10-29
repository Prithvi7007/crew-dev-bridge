#!/usr/bin/env bash
# One-time repair for the REA Lite v1 local-port failure; never invoked by MCP.
# Requires reviewed bridge main. Does not reinstall REA or touch other services.
set -euo pipefail

[[ "$EUID" == 0 ]] || { echo "Run as root after review" >&2; exit 1; }
SRC="/opt/crew-dev-bridge"
DEST="/opt/rea-lite"
ORIGINAL_MERGE="0c41d321bbeefd4287aa70102477a4ebac0729fe"

[[ "$(realpath "$(dirname "$0")/../../..")" == "$SRC" ]] || {
    echo "Installer must run from the bridge checkout at $SRC" >&2; exit 1;
}
[[ -d "$SRC/.git" && -d "$DEST" && ! -L "$DEST" ]] || {
    echo "Expected the existing, separate bridge and REA Lite checkouts" >&2; exit 1;
}
[[ "$(git -C "$SRC" branch --show-current)" == main ]] || {
    echo "Review and merge the fix before repair" >&2; exit 1;
}
[[ -z "$(git -C "$SRC" status --porcelain)" ]] || {
    echo "Bridge checkout must be clean" >&2; exit 1;
}
[[ -f "$DEST/compose.yaml" && ! -L "$DEST/compose.yaml" ]] || {
    echo "Missing or symlinked existing Compose configuration" >&2; exit 1;
}
[[ ! -e "$DEST/nginx.conf" && ! -L "$DEST/nginx.conf" ]] || {
    echo "Ingress already configured or unexpected nginx.conf exists" >&2; exit 1;
}
git -C "$SRC" show "$ORIGINAL_MERGE:deploy/rea-lite/compose.yaml" | cmp -s - "$DEST/compose.yaml" || {
    echo "Existing Compose differs from the reviewed v1 release; refusing overwrite" >&2; exit 1;
}

# Fail closed if anything is already using the intended localhost endpoint.
python3 - <<'PY'
import socket
with socket.socket() as sock:
    try:
        sock.bind(("127.0.0.1", 18765))
    except OSError as exc:
        raise SystemExit("127.0.0.1:18765 is already in use; investigate before repair") from exc
PY

SOURCE="$SRC/deploy/rea-lite"
for name in compose.yaml nginx.conf; do
    [[ -f "$SOURCE/$name" && ! -L "$SOURCE/$name" ]] || {
        echo "Missing or symlinked approved source $name" >&2; exit 1;
    }
done

install -o root -g root -m 0644 "$DEST/compose.yaml" "$DEST/compose.yaml.before-ingress"
install -o root -g root -m 0644 "$SOURCE/compose.yaml" "$DEST/compose.yaml"
install -o root -g root -m 0644 "$SOURCE/nginx.conf" "$DEST/nginx.conf"
cd "$DEST"
docker compose config --quiet

# Existing REA container remains untouched. Only add the restricted ingress.
docker compose up -d --no-deps ingress
if ! python3 scripts/smoke_test.py --retries 12; then
    echo "Ingress installed but REA MCP did not pass handshake." >&2
    echo "Inspect: cd /opt/rea-lite && docker compose ps -a && docker compose logs --tail=80 rea ingress" >&2
    exit 1
fi
echo "PASS: private REA Lite MCP now responds through isolated ingress"
echo "No public port or ChatGPT tunnel was created"
