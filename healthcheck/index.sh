#!/bin/bash

set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# The admin panel is the control plane and must stay reachable while
# lightweight identities are provisioning or recovering. When enabled,
# consider the container healthy as soon as the admin HTTP server responds.
if [ "${ADMIN_ENABLED:-false}" = "true" ]; then
    if curl -fsS --max-time 3 "http://127.0.0.1:${ADMIN_PORT:-9090}/health" >/dev/null 2>&1; then
        exit 0
    fi
fi

# Without the admin panel, require at least one verified WARP tunnel.
bash "$DIR/connected-to-warp.sh"
