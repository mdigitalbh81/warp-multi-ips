#!/bin/bash
# Helper script: starts a single lightweight wireproxy instance.
# Called by entrypoint.sh as: /start-wireproxy-instance.sh <instance> <port> [licenses_csv] [timeout]
set -euo pipefail

if [ -f "/warp-common.sh" ]; then
    . /warp-common.sh
elif [ -f "$(dirname "${BASH_SOURCE[0]}")/warp-common.sh" ]; then
    . "$(dirname "${BASH_SOURCE[0]}")/warp-common.sh"
fi

INSTANCE=${1:?"Instance number required"}
PORT=${2:?"Port number required"}
LICENSE_KEYS_CSV=${3:-}
CONNECT_TIMEOUT=${4:-30}

# Handle positional arguments flexibility (if 3rd is numeric timeout)
if [[ "$LICENSE_KEYS_CSV" =~ ^[0-9]+$ ]] && [ -z "${4:-}" ]; then
    CONNECT_TIMEOUT="$LICENSE_KEYS_CSV"
    LICENSE_KEYS_CSV=""
fi

CONNECT_TIMEOUT=${CONNECT_TIMEOUT:-30}

DATA_DIR="${WARP_DATA_DIR:-/var/lib/cloudflare-warp}/lightweight/instance-${INSTANCE}"
ACCOUNT_FILE="${DATA_DIR}/wgcf-account.toml"
PROFILE_FILE="${DATA_DIR}/wgcf-profile.conf"
CONF_FILE="${DATA_DIR}/wireproxy.conf"
EGRESS_FILE="${DATA_DIR}/egress.json"
PID_FILE="/tmp/wireproxy-instance-${INSTANCE}.pid"
WARP_PID_FILE="/tmp/warp-instance-${INSTANCE}.pid"

echo "[Instance ${INSTANCE}] Starting lightweight wireproxy (proxy port: ${PORT})..."

# Create instance directory with restricted permissions (0700)
if [ -w "$(dirname "$DATA_DIR")" ]; then
    mkdir -p "$DATA_DIR"
    chmod 700 "$DATA_DIR"
else
    sudo mkdir -p "$DATA_DIR"
    sudo chmod 700 "$DATA_DIR"
    sudo chown -R "$(id -u):$(id -g)" "$DATA_DIR" 2>/dev/null || true
fi

# Check if registration identity already exists
if [ ! -f "$ACCOUNT_FILE" ] || [ ! -f "$PROFILE_FILE" ]; then
    echo "[Instance ${INSTANCE}] No existing wgcf profile found, registering new device..."
    cd "$DATA_DIR"
    
    REG_OK=false
    MAX_REG_ATTEMPTS=5
    for attempt in $(seq 1 $MAX_REG_ATTEMPTS); do
        echo "[Instance ${INSTANCE}] Registration attempt ${attempt}/${MAX_REG_ATTEMPTS}..."
        # Run wgcf register without exposing secrets to console
        if wgcf register --accept-tos >/dev/null 2>&1; then
            REG_OK=true
            break
        fi
        BACKOFF=$(( 3 * attempt + (RANDOM % 3) ))
        echo "[Instance ${INSTANCE}] Registration attempt failed, retrying in ${BACKOFF}s..."
        sleep $BACKOFF
    done
    
if [ "$REG_OK" = false ]; then
    echo "[Instance ${INSTANCE}] Error: failed to register with Cloudflare after ${MAX_REG_ATTEMPTS} attempts"
    exit 1
fi

    # Apply license key if provided (without echoing the key)
    if [ -n "$LICENSE_KEYS_CSV" ]; then
        IFS=',' read -ra LICENSE_ARRAY <<< "$LICENSE_KEYS_CSV"
        if [ ${#LICENSE_ARRAY[@]} -gt 0 ]; then
            KEY_INDEX=$((INSTANCE % ${#LICENSE_ARRAY[@]}))
            INSTANCE_LICENSE="${LICENSE_ARRAY[$KEY_INDEX]}"
            [ -n "$INSTANCE_LICENSE" ] && wgcf update --license "$INSTANCE_LICENSE" >/dev/null 2>&1 || true
        fi
    fi

if ! wgcf generate >/dev/null 2>&1; then
        echo "[Instance ${INSTANCE}] Error: failed to generate WireGuard profile"
        exit 1
    fi
    chmod 600 "$ACCOUNT_FILE" "$PROFILE_FILE" 2>/dev/null || true
    echo "[Instance ${INSTANCE}] Successfully registered and generated profile"
else
    echo "[Instance ${INSTANCE}] Reusing existing persisted wgcf profile"
fi

# Build wireproxy.conf from wgcf-profile.conf
# Strip any existing Socks5 section and append current port configuration
grep -v -E '^\[Socks5\]|^BindAddress' "$PROFILE_FILE" > "$CONF_FILE"
cat <<EOF >> "$CONF_FILE"

[Socks5]
BindAddress = 127.0.0.1:${PORT}
EOF
chmod 600 "$CONF_FILE" 2>/dev/null || true

# Cleanup old PID files if any
rm -f "$PID_FILE" "$WARP_PID_FILE" 2>/dev/null || true

# Start wireproxy
wireproxy -c "$CONF_FILE" >/dev/null 2>&1 &
WP_PID=$!
echo "$WP_PID" > "$PID_FILE"
echo "$WP_PID" > "$WARP_PID_FILE"

cleanup() {
    if kill -0 "$WP_PID" 2>/dev/null; then
        kill "$WP_PID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE" "$WARP_PID_FILE" 2>/dev/null || true
}
trap cleanup SIGTERM SIGINT EXIT

# Wait for internal SOCKS port listener
ELAPSED=0
READY=false
while [ "$ELAPSED" -lt "$CONNECT_TIMEOUT" ]; do
    if curl -fsS --max-time 2 --socks5-hostname "127.0.0.1:${PORT}" "https://www.cloudflare.com/cdn-cgi/trace" 2>/dev/null | grep -qE '^warp=(on|plus)'; then
        READY=true
        break
    fi
    sleep 2
    ELAPSED=$((ELAPSED + 2))
done

if [ "$READY" = true ]; then
    echo "[Instance ${INSTANCE}] wireproxy ready and connected to WARP after ${ELAPSED}s"
    # Detect public IPv6 egress
    IP6=$(curl -fsS --max-time 6 --socks5-hostname "127.0.0.1:${PORT}" "https://api6.ipify.org" 2>/dev/null || true)
    if [[ "$IP6" =~ : ]]; then
        echo "[Instance ${INSTANCE}] IPv6 Egress: ${IP6}"
        PREV_IP=""
        if [ -f "$EGRESS_FILE" ]; then
            PREV_IP=$(python3 -c "import json, sys; d=json.load(open(sys.argv[1])); print(d.get('current_ipv6', ''))" "$EGRESS_FILE" 2>/dev/null || true)
        fi
        NOW_ISO=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
        python3 -c "import json, sys; json.dump({'current_ipv6': sys.argv[1], 'previous_ipv6': sys.argv[2], 'last_change': sys.argv[3]}, open(sys.argv[4], 'w'), indent=2)" \
            "$IP6" "$PREV_IP" "$NOW_ISO" "$EGRESS_FILE" 2>/dev/null || true
        chmod 600 "$EGRESS_FILE" 2>/dev/null || true
    fi
else
    echo "[Instance ${INSTANCE}] Warning: wireproxy not fully ready after ${CONNECT_TIMEOUT}s, continuing anyway..."
fi

wait "$WP_PID"
