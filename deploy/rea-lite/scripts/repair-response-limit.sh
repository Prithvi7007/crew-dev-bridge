#!/usr/bin/env bash
# Narrow repair for REA 6.8.0 refusing its old 1 MiB response setting.
# This script does NOT install Docker, rebuild the image, or touch CREW/wedding.
# Requires reviewed code merged to bridge main and an exact v2 REA deployment.
set -euo pipefail

[[ "$EUID" == 0 ]] || { echo "Run as root after reviewing the merged PR" >&2; exit 1; }
SRC="/opt/crew-dev-bridge"
DEST="/opt/rea-lite"
PREVIOUS_MERGE="64e6a3a345905e3cb3d4cb348d67124b36372ecb"

[[ "$(realpath "$(dirname "$0")/../../..")" == "$SRC" ]] || {
    echo "Run the versioned repair script from $SRC" >&2; exit 1;
}
[[ -d "$SRC/.git" && -d "$DEST" && ! -L "$DEST" ]] || {
    echo "Expected reviewed bridge checkout and existing REA Lite installation" >&2; exit 1;
}
[[ "$(git -C "$SRC" branch --show-current)" == "main" ]] || {
    echo "Merge and checkout reviewed main before repair" >&2; exit 1;
}
[[ -z "$(git -C "$SRC" status --porcelain)" ]] || {
    echo "Bridge checkout must be clean" >&2; exit 1;
}

for name in compose.yaml Dockerfile nginx.conf; do
    [[ -f "$DEST/$name" && ! -L "$DEST/$name" ]] || {
        echo "Missing or symlinked existing REA Lite file: $name" >&2; exit 1;
    }
    [[ -f "$SRC/deploy/rea-lite/$name" && ! -L "$SRC/deploy/rea-lite/$name" ]] || {
        echo "Missing or symlinked reviewed source: $name" >&2; exit 1;
    }
done
for name in compose.yaml Dockerfile nginx.conf; do
    git -C "$SRC" show "$PREVIOUS_MERGE:deploy/rea-lite/$name" | cmp -s - "$DEST/$name" || {
        echo "Existing $name does not match merged REA Lite v2; refusing to overwrite" >&2; exit 1;
    }
done
for name in compose.yaml Dockerfile; do
    [[ ! -e "$DEST/$name.before-response-limit" && ! -L "$DEST/$name.before-response-limit" ]] || {
        echo "Repair backup already exists; refusing to overwrite" >&2; exit 1;
    }
done

cd "$DEST"
# Do not upgrade or build the image: the Compose environment override repairs
# the existing image's 1 MiB value. Dockerfile is corrected for future builds.
for name in compose.yaml Dockerfile; do
    install -o root -g root -m 0644 "$DEST/$name" "$DEST/$name.before-response-limit"
    install -o root -g root -m 0644 "$SRC/deploy/rea-lite/$name" "$DEST/$name"
done

docker compose config --quiet
# Recreate only REA to apply the new environment variable; do not delete volumes.
docker compose up -d --no-deps --force-recreate rea
# Nginx may have cached the previous REA container IP during startup.
docker compose restart ingress

if ! python3 scripts/smoke_test.py --retries 12; then
    echo "Response limit corrected, but the live MCP handshake still failed." >&2
    echo "Inspect: cd /opt/rea-lite && docker compose ps -a && docker compose logs --tail=100 --no-color rea ingress" >&2
    exit 1
fi
echo "PASS: REA Lite MCP is responding at localhost:18765/mcp"
echo "No CREW or wedding service was restarted; ChatGPT tunnel is still separate."
