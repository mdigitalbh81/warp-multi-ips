#!/bin/bash
# Copyright (c) 2025 Ercin Dedeoglu
# Licensed under CC BY-NC 4.0 (Attribution-NonCommercial)
# https://github.com/ErcinDedeoglu/cloudflare-warp
#
# Lightweight wireproxy-only entrypoint.
# All WARP connectivity is handled by wgcf + wireproxy.

set -euo pipefail

if [ -f "/warp-common.sh" ]; then
    . /warp-common.sh
elif [ -f "$(dirname "${BASH_SOURCE[0]}")/warp-common.sh" ]; then
    . "$(dirname "${BASH_SOURCE[0]}")/warp-common.sh"
fi

# Preserve initial explicit environment variables before loading configs
ENV_WARP_INSTANCES="${WARP_INSTANCES+x}"
ENV_WARP_INSTANCES_VALUE="${WARP_INSTANCES:-}"
ENV_PROXY_MODE="${PROXY_MODE+x}"
ENV_PROXY_MODE_VALUE="${PROXY_MODE:-}"
ENV_PROXY_BASE_PORT="${PROXY_BASE_PORT+x}"
ENV_PROXY_BASE_PORT_VALUE="${PROXY_BASE_PORT:-}"
ENV_PROXY_MAX_RPS="${PROXY_MAX_RPS+x}"
ENV_PROXY_MAX_RPS_VALUE="${PROXY_MAX_RPS:-}"
ENV_WARP_CONNECT_TIMEOUT="${WARP_CONNECT_TIMEOUT+x}"
ENV_WARP_CONNECT_TIMEOUT_VALUE="${WARP_CONNECT_TIMEOUT:-}"
ENV_AUTO_REFRESH_INTERVAL="${AUTO_REFRESH_INTERVAL+x}"
ENV_AUTO_REFRESH_INTERVAL_VALUE="${AUTO_REFRESH_INTERVAL:-}"
ENV_PROXY_HOST_OMNIROUTE="${PROXY_HOST_OMNIROUTE:-${PROXY_HOST:-}}"

init_admin_config
load_admin_config
validate_runtime_config

# Migrate old config that may contain warp_engine=official
if [ "${ADMIN_ENABLED:-false}" = "true" ] && [ -f "$ADMIN_CONFIG_FILE" ]; then
    if command -v jq &>/dev/null; then
        _old_engine=$(jq -r '.warp_engine // ""' "$ADMIN_CONFIG_FILE" 2>/dev/null || true)
        if [ -n "$_old_engine" ]; then
            jq 'del(.warp_engine)' "$ADMIN_CONFIG_FILE" > "${ADMIN_CONFIG_FILE}.tmp" && \
                mv "${ADMIN_CONFIG_FILE}.tmp" "$ADMIN_CONFIG_FILE" 2>/dev/null || true
            echo "Migration: removed deprecated warp_engine field from admin config"
        fi
    fi
fi

write_op_state() {
    local status="$1"
    local msg="$2"
    local cur="${3:-0}"
    local tot="${4:-0}"
    local err="${5:-}"
    cat <<EOF > /tmp/operation-state.json
{
    "status": "${status}",
    "message": "${msg}",
    "current": ${cur},
    "total": ${tot},
    "error": "${err}",
    "timestamp": "$(date +"%Y-%m-%dT%H:%M:%SZ")"
}
EOF
}

ADMIN_PID=""
if [ "${ADMIN_ENABLED:-false}" = "true" ]; then
    sudo chown -R warp:warp /var/lib/cloudflare-warp 2>/dev/null || true
    write_admin_env_file
    write_op_state "running" "Starting admin panel and initializing instances..." 0 "${WARP_INSTANCES:-10}"
    echo "Starting admin panel on :${ADMIN_PORT:-9090}"
    python3 /admin/server.py &
    ADMIN_PID=$!
fi

# ==============================================================================
# MULTI-INSTANCE LIGHTWEIGHT MODE
# ==============================================================================

echo "========================================"
echo "  Multi-Instance WARP Lightweight Mode"
echo "  Instances: ${WARP_INSTANCES}"
echo "  Engine: wireproxy (wgcf + wireproxy)"
if [ "$PROXY_MODE" = "dedicated" ]; then
    echo "  Proxy mode: dedicated (1 port per instance, base: ${PROXY_BASE_PORT})"
else
    echo "  Proxy mode: round-robin"
fi
echo "========================================"
echo ""

write_op_state "running" "Starting ${WARP_INSTANCES} lightweight instances..." 0 "$WARP_INSTANCES"

# ---- start each wireproxy instance ----
INSTANCE_PIDS=()
for i in $(seq 0 $((WARP_INSTANCES - 1))); do
    PORT=$((40000 + i))
    write_op_state "running" "Starting instance $((i + 1))/${WARP_INSTANCES}..." "$((i + 1))" "$WARP_INSTANCES"

    /start-wireproxy-instance.sh \
        "$i" "$PORT" "" "${WARP_CONNECT_TIMEOUT:-30}" &
    INSTANCE_PIDS+=($!)

    LW_DIR="${WARP_DATA_DIR:-/var/lib/cloudflare-warp}/lightweight/instance-${i}"
    if [ ! -f "$LW_DIR/wgcf-profile.conf" ]; then
        if [ "$i" -lt $((WARP_INSTANCES - 1)) ]; then
            sleep "${LIGHTWEIGHT_REGISTRATION_DELAY:-10}"
        fi
    else
        sleep 0.5
    fi
done

# ---- verify each instance is connected to WARP (parallel) ----
write_op_state "running" "Verifying WARP instances..." "$WARP_INSTANCES" "$WARP_INSTANCES"

echo ""
echo "Verifying WARP instances (parallel)..."

READY_COUNT=0
MAX_VERIFY_WAIT=90
VERIFY_DIR=$(mktemp -d)
VERIFY_PIDS=()

for i in $(seq 0 $((WARP_INSTANCES - 1))); do
    (
        PORT=$((40000 + i))
        WAIT=0
        while [ "$WAIT" -lt "$MAX_VERIFY_WAIT" ]; do
            if curl --connect-timeout 4 --socks5-hostname "127.0.0.1:${PORT}" \
                "https://cloudflare.com/cdn-cgi/trace" 2>/dev/null | grep -qE 'warp=(on|plus)'; then
                IP6=$(curl -fsS --max-time 6 --socks5-hostname "127.0.0.1:${PORT}" \
                    "https://api6.ipify.org" 2>/dev/null || true)
                if [[ "$IP6" == *:* ]]; then
                    echo "$IP6" > "${VERIFY_DIR}/${i}.ip6"
                fi
                echo "OK" > "${VERIFY_DIR}/${i}"
                exit 0
            fi
            sleep 3
            WAIT=$((WAIT + 3))
        done
        exit 1
    ) &
    VERIFY_PIDS+=($!)
done

for pid in "${VERIFY_PIDS[@]}"; do
    wait "$pid" 2>/dev/null || true
done

# IPv6 uniqueness check
if [ "${LIGHTWEIGHT_REQUIRE_UNIQUE_EGRESS:-true}" = "true" ]; then
    declare -A SEEN_IP6
    for i in $(seq 0 $((WARP_INSTANCES - 1))); do
        if [ -f "${VERIFY_DIR}/${i}.ip6" ]; then
            ADDR=$(cat "${VERIFY_DIR}/${i}.ip6")
            if [ -n "${SEEN_IP6[$ADDR]+x}" ]; then
                echo "Warning: IPv6 collision detected between instance ${SEEN_IP6[$ADDR]} and instance ${i} (${ADDR})"
                rm -f "${VERIFY_DIR}/${i}"
            else
                SEEN_IP6["$ADDR"]="$i"
            fi
        fi
    done
fi

for i in $(seq 0 $((WARP_INSTANCES - 1))); do
    PORT=$((40000 + i))
    if [ -f "${VERIFY_DIR}/${i}" ]; then
        echo "  Instance ${i}: OK (port ${PORT})"
        READY_COUNT=$((READY_COUNT + 1))
    else
        echo "  Instance ${i}: FAILED (port ${PORT} not responding after ${MAX_VERIFY_WAIT}s)"
    fi
done

echo ""
echo "${READY_COUNT}/${WARP_INSTANCES} WARP instances ready"

if [ "$READY_COUNT" -eq 0 ]; then
    rm -rf "$VERIFY_DIR"
    write_op_state "error" "No WARP instances started successfully" 0 "$WARP_INSTANCES" "All instances failed to connect"

    if [ "${ADMIN_ENABLED:-false}" != "true" ]; then
        echo "Error: no WARP instances started successfully. Exiting."
        exit 1
    fi
    echo "Warning: 0 instances ready. Keeping admin panel running for diagnostics and reconfiguration."
else
    write_op_state "idle" "Ready (${READY_COUNT}/${WARP_INSTANCES} healthy)" "$READY_COUNT" "$WARP_INSTANCES"
fi

# ---- generate GOST config ----
if [ "$PROXY_MODE" = "dedicated" ]; then
    generate_gost_config_dedicated "$VERIFY_DIR"
else
    generate_gost_config_roundrobin "$VERIFY_DIR"
fi
rm -rf "$VERIFY_DIR"

# ---- summary ----
echo ""
if [ "$PROXY_MODE" = "dedicated" ]; then
    echo "=== Proxy Endpoints (dedicated, ${READY_COUNT}/${WARP_INSTANCES} instances ready) ==="
    for i in $(seq 0 $((WARP_INSTANCES - 1))); do
        DPORT=$((PROXY_BASE_PORT + i))
        echo "  SOCKS5 instance $((i+1)) : :${DPORT} -> WARP $((i+1))"
    done
    echo "  ---"
    echo "  SOCKS5 (Direct) : :1081"
    echo "  HTTP   (Direct) : :8081"
    echo "  SS     (Direct) : :8389"
    if [ -n "$PROXY_USER" ]; then
        echo "  Auth: ${PROXY_USER}:***"
    fi
    echo "========================================================="
else
    echo "=== Proxy Endpoints (round-robin across ${READY_COUNT} instances) ==="
    echo "  SOCKS5 (WARP)   : :1080"
    echo "  HTTP   (WARP)   : :8080"
    echo "  SS     (WARP)   : :8388"
    echo "  SOCKS5 (Direct) : :1081"
    echo "  HTTP   (Direct) : :8081"
    echo "  SS     (Direct) : :8389"
    if [ -n "$PROXY_USER" ]; then
        echo "  Auth: ${PROXY_USER}:***"
    fi
    echo "========================================================="
fi
echo ""

# ---- cleanup on shutdown ----
cleanup() {
    echo "Shutting down ${WARP_INSTANCES} wireproxy instances..."
    for pid in "${INSTANCE_PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    sudo pkill -f "wireproxy" 2>/dev/null || true
    kill "$ADMIN_PID" 2>/dev/null || true
    kill "$GOST_PID" 2>/dev/null || true
    kill "$WATCHDOG_PID" 2>/dev/null || true
    wait
}
trap cleanup SIGTERM SIGINT

# ---- start watchdog (multi-instance only) ----
WATCHDOG_PID=""
if [ "$WARP_INSTANCES" -gt 1 ] && [ "${WARP_WATCHDOG_ENABLED:-true}" = "true" ]; then
    echo "Starting watchdog for ${WARP_INSTANCES} instances..."
    /watchdog.sh &
    WATCHDOG_PID=$!
elif [ "$WARP_INSTANCES" -gt 1 ]; then
    echo "Watchdog disabled (WARP_WATCHDOG_ENABLED=false)"
fi

# ---- start GOST (foreground keeps container alive) ----
if [ "$PROXY_MODE" = "dedicated" ]; then
    echo "Starting GOST proxy (dedicated mode, ${READY_COUNT} instances)..."
else
    echo "Starting GOST proxy (round-robin across ${READY_COUNT} instances)..."
fi

while true; do
    gost -C /tmp/gost-config.yaml &
    GOST_PID=$!
    RESTART_REQUESTED=false
    while kill -0 "$GOST_PID" 2>/dev/null; do
        if [ -f "${GOST_RESTART_FILE:-/tmp/gost-restart-request}" ]; then
            echo "Restarting GOST after admin config change..."
            rm -f "${GOST_RESTART_FILE:-/tmp/gost-restart-request}"
            kill "$GOST_PID" 2>/dev/null || true
            wait "$GOST_PID" 2>/dev/null || true
            RESTART_REQUESTED=true
            break
        fi
        sleep 1
    done
    if [ "$RESTART_REQUESTED" = true ]; then
        continue
    fi
    wait "$GOST_PID"
    exit $?
done
