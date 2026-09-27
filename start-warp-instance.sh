#!/bin/bash
# Compatibility shim retained for callers that still invoke /start-warp-instance.sh.
# This branch is wireproxy-only: the official warp-svc engine was intentionally removed.
set -euo pipefail

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
if [ -x /start-wireproxy-instance.sh ]; then
    exec /start-wireproxy-instance.sh "$@"
fi
exec "${SCRIPT_DIR}/start-wireproxy-instance.sh" "$@"
