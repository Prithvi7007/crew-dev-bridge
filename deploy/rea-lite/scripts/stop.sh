#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose down
echo "Stopped only rea-lite; evidence volume and input artifacts retained."
