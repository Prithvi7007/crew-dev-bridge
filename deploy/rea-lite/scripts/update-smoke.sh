#!/usr/bin/env bash
# Refresh only the local REA MCP smoke-test script after reviewing the PR.
# No Docker commands, app changes, network/service restarts, or secret access.
set -euo pipefail

[[ "$EUID" == 0 ]] || { echo "Run as root after reviewing and merging" >&2; exit 1; }
SRC="/opt/crew-dev-bridge"
DEST="/opt/rea-lite"
OLD_INSTALL="0c41d321bbeefd4287aa70102477a4ebac0729fe"
PREVIOUS_MERGE="96eaea0935e7cce3d665ef3d916e26e8f07099e1"

[[ "$(realpath "$(dirname "$0")/../../..")" == "$SRC" ]] || {
    echo "Expected approved bridge checkout at $SRC" >&2; exit 1;
}
[[ -d "$SRC/.git" && -d "$DEST/scripts" && ! -L "$DEST" && ! -L "$DEST/scripts" ]] || {
    echo "Expected existing separate REA Lite installation" >&2; exit 1;
}
[[ "$(git -C "$SRC" branch --show-current)" == "main" ]] || {
    echo "Merge and checkout reviewed bridge main before refresh" >&2; exit 1;
}
[[ -z "$(git -C "$SRC" status --porcelain)" ]] || {
    echo "Bridge checkout must be clean" >&2; exit 1;
}

target="$DEST/scripts/smoke_test.py"
source="$SRC/deploy/rea-lite/scripts/smoke_test.py"
backup="$DEST/scripts/smoke_test.py.before-large-response"
[[ -f "$target" && ! -L "$target" && -f "$source" && ! -L "$source" ]] || {
    echo "Missing or unsafe smoke-test script" >&2; exit 1;
}
[[ ! -e "$backup" && ! -L "$backup" ]] || {
    echo "Smoke-test backup already exists; refusing a second refresh" >&2; exit 1;
}
# Previous repair revisions did not refresh this local script. Accept the exact
# old original installation or PR #23 revision; never overwrite custom edits.
if ! (git -C "$SRC" show "$OLD_INSTALL:deploy/rea-lite/scripts/smoke_test.py" | cmp -s - "$target") \
   && ! (git -C "$SRC" show "$PREVIOUS_MERGE:deploy/rea-lite/scripts/smoke_test.py" | cmp -s - "$target"); then
    echo "Local smoke-test script differs from reviewed versions; refusing overwrite" >&2
    exit 1
fi

install -o root -g root -m 0644 "$target" "$backup"
install -o root -g root -m 0644 "$source" "$target"
echo "Updated only the REA MCP smoke tester; REA, ingress and other services were not restarted."
cd "$DEST"
python3 scripts/smoke_test.py
