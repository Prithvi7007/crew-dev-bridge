#!/usr/bin/env bash
# Human-reviewed, one-time, independent REA installation. NOT invoked by MCP.
set -euo pipefail
[[ "$EUID" == 0 ]] || { echo "Run as root after review" >&2; exit 1; }
SRC="/opt/crew-dev-bridge"
DEST="/opt/rea-lite"
ACTUAL="$(realpath "$(dirname "$0")/../../..")"
[[ "$ACTUAL" == "$SRC" && -d "$SRC/.git" ]] || {
  echo "Expected source checkout at $SRC" >&2; exit 1;
}
[[ "$(git -C "$SRC" branch --show-current)" == "main" ]] || {
  echo "Install only from reviewed main" >&2; exit 1;
}
[[ -z "$(git -C "$SRC" status --porcelain)" ]] || {
  echo "Bridge checkout must be clean" >&2; exit 1;
}
if [[ -e "$DEST" || -L "$DEST" ]]; then
  echo "Existing $DEST detected. Refusing overwrite or implicit upgrade." >&2
  exit 1
fi
SOURCE="$SRC/deploy/rea-lite"
for file in Dockerfile compose.yaml nginx.conf scripts/preflight.sh scripts/smoke_test.py scripts/stop.sh; do
  [[ -f "$SOURCE/$file" && ! -L "$SOURCE/$file" ]] || {
    echo "Missing or symlinked deployment file: $file" >&2; exit 1;
  }
done

# Copy only dedicated, reviewed files to a separate root-owned project.
install -d -o root -g root -m 0755 "$DEST" "$DEST/scripts" "$DEST/artifacts"
install -o root -g root -m 0644 "$SOURCE/Dockerfile" "$DEST/Dockerfile"
install -o root -g root -m 0644 "$SOURCE/compose.yaml" "$DEST/compose.yaml"
install -o root -g root -m 0644 "$SOURCE/nginx.conf" "$DEST/nginx.conf"
install -o root -g root -m 0644 "$SOURCE/scripts/smoke_test.py" "$DEST/scripts/smoke_test.py"
for script in preflight.sh stop.sh; do
  install -o root -g root -m 0755 "$SOURCE/scripts/$script" "$DEST/scripts/$script"
done
cd "$DEST"
bash scripts/preflight.sh
echo "Building and starting isolated rea-lite and localhost-only ingress..."
docker compose build rea
docker compose up -d rea ingress
python3 scripts/smoke_test.py --retries 15
echo "REA Lite MCP is responding privately at http://127.0.0.1:18765/mcp"
echo "ChatGPT remote MCP tunnel must be configured separately; no public port was opened."
