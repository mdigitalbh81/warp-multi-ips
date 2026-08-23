#!/bin/bash

# Copyright (c) 2025 Ercin Dedeoglu
# Licensed under CC BY-NC 4.0 (Attribution-NonCommercial)
# https://github.com/ErcinDedeoglu/cloudflare-warp
#
# Commercial use is prohibited. For personal/educational use,
# you must provide public attribution to this project.

set -e

. /warp-common.sh

WARP_INSTANCES=${WARP_INSTANCES:-1}
PROXY_MODE=${PROXY_MODE:-round-robin}
PROXY_BASE_PORT=${PROXY_BASE_PORT:-2080}
PROXY_MAX_RPS=${PROXY_MAX_RPS:-50}
WARP_CONNECT_TIMEOUT=${WARP_CONNECT_TIMEOUT:-30}
AUTO_REFRESH_INTERVAL=${AUTO_REFRESH_INTERVAL:-60}
# PROXY_HOST_OMNIROUTE (preferred) with PROXY_HOST fallback (deprecated)
PROXY_HOST_OMNIROUTE=${PROXY_HOST_OMNIROUTE:-${PROXY_HOST:-}}

init_admin_config
load_admin_config
validate_runtime_config
if [ "${ADMIN_ENABLED:-false}" = "true" ]; then
    sudo chown -R warp:warp /var/lib/cloudflare-warp
fi
write_admin_env_file

# ---- Parse license key(s) — WARP_LICENSE_KEY accepts comma-separated values ----
LICENSE_KEYS=()
if [ -n "${WARP_LICENSE_KEY:-}" ]; then
    IFS=',' read -ra _RAW_KEYS <<< "$WARP_LICENSE_KEY"
    for _k in "${_RAW_KEYS[@]}"; do
        _k=$(echo "$_k" | xargs)
        [ -n "$_k" ] && LICENSE_KEYS+=("$_k")
    done
fi
NUM_KEYS=${#LICENSE_KEYS[@]}

# Reconstruct cleaned CSV for passing to instance scripts and change detection
LICENSE_KEYS_CSV=""
if [ "$NUM_KEYS" -gt 0 ]; then
    LICENSE_KEYS_CSV=$(IFS=','; echo "${LICENSE_KEYS[*]}")
fi
export LICENSE_KEYS_CSV

# ---- Zero Trust enrollment mode (service token auth) ----
ZT_MODE=false
if [ -n "${WARP_ORG:-}" ]; then
    if [ -z "${WARP_AUTH_CLIENT_ID:-}" ] || [ -z "${WARP_AUTH_CLIENT_SECRET:-}" ]; then
        echo "Error: WARP_ORG is set but WARP_AUTH_CLIENT_ID and/or WARP_AUTH_CLIENT_SECRET are missing."
        echo "All three variables are required for Zero Trust enrollment."
        exit 1
    fi
    if [ "$NUM_KEYS" -gt 0 ]; then
        echo "Error: WARP_ORG and WARP_LICENSE_KEY are mutually exclusive."
        echo "Use WARP_ORG for Zero Trust enrollment OR WARP_LICENSE_KEY for WARP+ — not both."
        exit 1
    fi
    ZT_MODE=true
fi

# ---- helper: write MDM XML for Zero Trust enrollment ----
# warp-svc reads mdm.xml from its data directory on startup and auto-enrolls
# into the Zero Trust org using the service token — no browser required.
# $1 = data directory, $2 = proxy port
write_mdm_xml() {
    local data_dir="$1"
    local port="$2"
    local mdm_file="${data_dir}/mdm.xml"
    sudo tee "$mdm_file" > /dev/null <<MDMEOF
<dict>
  <key>organization</key>
  <string>${WARP_ORG}</string>
  <key>auth_client_id</key>
  <string>${WARP_AUTH_CLIENT_ID}</string>
  <key>auth_client_secret</key>
  <string>${WARP_AUTH_CLIENT_SECRET}</string>
  <key>service_mode</key>
  <string>proxy</string>
  <key>proxy_port</key>
  <integer>${port}</integer>
  <key>auto_connect</key>
  <integer>1</integer>
  <key>switch_locked</key>
  <true/>
  <key>onboarding</key>
  <false/>
</dict>
MDMEOF
    echo "MDM config written to ${mdm_file} (org: ${WARP_ORG}, port: ${port})"
}

# ==============================================================================
# SINGLE INSTANCE MODE (default, fully backward-compatible)
# ==============================================================================
if [ "$WARP_INSTANCES" -eq 1 ] && [ "$PROXY_MODE" != "dedicated" ] && [ "${ADMIN_ENABLED:-false}" != "true" ]; then

    # start dbus
    sudo mkdir -p /run/dbus
    if [ -f /run/dbus/pid ]; then
        sudo rm /run/dbus/pid
    fi
    sudo dbus-daemon --config-file=/usr/share/dbus-1/system.conf

    # Write MDM config for Zero Trust (must happen before warp-svc reads data dir)
    if [ "$ZT_MODE" = true ]; then
        write_mdm_xml "/var/lib/cloudflare-warp" 40000
    fi

    # start the daemon
    sudo warp-svc --accept-tos &

    # wait for the daemon to be ready
    MAX_WAIT=${WARP_CONNECT_TIMEOUT:-30}
    INTERVAL=2
    ELAPSED=0

    echo "Waiting for WARP daemon to be ready (max ${MAX_WAIT}s)..."
    while [ $ELAPSED -lt $MAX_WAIT ]; do
        if warp-cli status 2>/dev/null | grep -qE "(Status|Connected)"; then
            echo "WARP daemon is ready after ${ELAPSED}s"
            break
        fi
        sleep $INTERVAL
        ELAPSED=$((ELAPSED + INTERVAL))
    done

    if [ $ELAPSED -ge $MAX_WAIT ]; then
        echo "Warning: WARP daemon may not be fully ready after ${MAX_WAIT}s, continuing anyway..."
    fi

    if [ "$ZT_MODE" = true ]; then
        # Zero Trust: warp-svc handles enrollment automatically via MDM config.
        # MDM sets service_mode=proxy and proxy_port=40000; connect as safety net.
        echo "Zero Trust mode: waiting for automatic enrollment via service token..."
        warp-cli --accept-tos connect 2>/dev/null || true
        echo "WARP Zero Trust proxy active on localhost:40000 (org: ${WARP_ORG})"
    else
        # register and apply license (tries all keys in order, stops on first success)
        STORED_KEY_FILE="/var/lib/cloudflare-warp/.license_key"

        apply_license_keys() {
            local label=$1
            for i in $(seq 0 $((NUM_KEYS - 1))); do
                local key="${LICENSE_KEYS[$i]}"
                echo "Trying license key $((i + 1))/${NUM_KEYS}..."
                local out
                out=$(warp-cli registration license "$key" 2>&1) && {
                    echo "Warp license ${label} (key $((i + 1)))!"
                    echo -n "$LICENSE_KEYS_CSV" | sudo tee "$STORED_KEY_FILE" > /dev/null
                    return 0
                } || {
                    echo "Key $((i + 1)) failed: ${out}"
                }
            done
            echo "All ${NUM_KEYS} license keys failed, running as free WARP"
            return 1
        }

        if [ ! -f /var/lib/cloudflare-warp/reg.json ]; then
            REG_OK=false
            MAX_REG_ATTEMPTS=10
            for attempt in $(seq 1 $MAX_REG_ATTEMPTS); do
                REG_OUT=$(warp-cli registration new 2>&1) && {
                    echo "Warp client registered!"
                    REG_OK=true
                    break
                } || {
                    # Exponential backoff with jitter: 2^attempt + random jitter, capped at 120s
                    BACKOFF=$(( (1 << attempt) + RANDOM % (1 << attempt) ))
                    [ "$BACKOFF" -gt 120 ] && BACKOFF=120
                    echo "Registration attempt ${attempt}/${MAX_REG_ATTEMPTS} failed: ${REG_OUT} (retrying in ${BACKOFF}s)"
                    sleep "$BACKOFF"
                }
            done
            if [ "$REG_OK" = false ]; then
                echo "Warning: registration failed after ${MAX_REG_ATTEMPTS} attempts, continuing without license..."
            fi
            if [ "$REG_OK" = true ] && [ "$NUM_KEYS" -gt 0 ]; then
                apply_license_keys "registered" || true
            fi
        else
            # Re-apply license if keys have changed since last registration
            STORED_KEYS=""
            [ -f "$STORED_KEY_FILE" ] && STORED_KEYS=$(sudo cat "$STORED_KEY_FILE" 2>/dev/null)
            if [ "$NUM_KEYS" -gt 0 ] && [ "$LICENSE_KEYS_CSV" != "$STORED_KEYS" ]; then
                echo "License key(s) changed, re-applying..."
                apply_license_keys "updated" || true
            fi
        fi

        # set proxy mode and connect
        warp-cli --accept-tos mode proxy
        warp-cli --accept-tos connect
        echo "WARP proxy mode active on localhost:40000"
    fi

    # disable qlog
    warp-cli --accept-tos debug qlog disable

    # Build GOST arguments
    GOST_LISTEN=":1080"
    GOST_OPTS=""

    if [ -n "$PROXY_USER" ] && [ -n "$PROXY_PASS" ]; then
        GOST_LISTEN="${PROXY_USER}:${PROXY_PASS}@:1080"
        echo "Proxy authentication enabled for user: ${PROXY_USER}"
    fi

    CLIMITER=${PROXY_MAX_CONN:-10}
    RLIMITER=${PROXY_MAX_RPS:-50}
    GOST_OPTS="climiter=${CLIMITER}&rlimiter=${RLIMITER}"

    if [ -n "$PROXY_ALLOWED_IPS" ]; then
        GOST_OPTS="${GOST_OPTS}&admission=~${PROXY_ALLOWED_IPS}"
        echo "IP whitelist enabled: ${PROXY_ALLOWED_IPS}"
    fi

    # Build HTTP proxy listen addresses
    HTTP_WARP_LISTEN=":8080"
    HTTP_DIRECT_LISTEN=":8081"
    if [ -n "$PROXY_USER" ] && [ -n "$PROXY_PASS" ]; then
        HTTP_WARP_LISTEN="${PROXY_USER}:${PROXY_PASS}@:8080"
        HTTP_DIRECT_LISTEN="${PROXY_USER}:${PROXY_PASS}@:8081"
    fi

    # Build direct proxy listen address
    DIRECT_LISTEN=":1081"
    if [ -n "$PROXY_USER" ] && [ -n "$PROXY_PASS" ]; then
        DIRECT_LISTEN="${PROXY_USER}:${PROXY_PASS}@:1081"
    fi

    # Start direct proxies (SOCKS5 on 1081, HTTP on 8081) - bypass WARP
    echo "Starting direct proxies on :1081 (SOCKS5) and :8081 (HTTP) -> Internet (no WARP)"
    gost -L "socks5://${DIRECT_LISTEN}?${GOST_OPTS}" -L "http://${HTTP_DIRECT_LISTEN}?${GOST_OPTS}" &

    # Start Shadowsocks servers (for mobile VPN clients)
    # Use PROXY_PASS if set, otherwise default to 'cloudflare-warp'
    SS_PASS=${PROXY_PASS:-cloudflare-warp}
    SS_METHOD=${SS_METHOD:-chacha20-ietf-poly1305}

    echo "Starting Shadowsocks servers:"
    echo "  - WARP exit on :8388 (method: ${SS_METHOD})"
    echo "  - Direct exit on :8389 (method: ${SS_METHOD})"

    # Shadowsocks through WARP
    gost -L "ss://${SS_METHOD}:${SS_PASS}@:8388?${GOST_OPTS}" -F socks5://127.0.0.1:40000 &

    # Shadowsocks direct (bypass WARP)
    gost -L "ss://${SS_METHOD}:${SS_PASS}@:8389?${GOST_OPTS}" &

    # Generate connection info for mobile apps
    echo ""
    echo "=== Shadowsocks Connection Info ==="
    echo "For mobile apps (Shadowsocks, Shadowrocket, v2rayNG):"
    echo "  Server: <YOUR_SERVER_IP>"
    echo "  Port (WARP): 8388"
    echo "  Port (Direct): 8389"
    if [ -n "$PROXY_PASS" ]; then
        echo "  Password: <your PROXY_PASS>"
    else
        echo "  Password: cloudflare-warp"
    fi
    echo "  Method: ${SS_METHOD}"
    echo "==================================="
    echo ""

    # Start WARP proxies (SOCKS5 on 1080, HTTP on 8080) - chain to WARP
    echo "Starting WARP proxies on :1080 (SOCKS5) and :8080 (HTTP) -> WARP proxy"
    gost -L "socks5://${GOST_LISTEN}?${GOST_OPTS}" -L "http://${HTTP_WARP_LISTEN}?${GOST_OPTS}" -F socks5://127.0.0.1:40000

    # Unreachable — gost above runs in the foreground
    exit 0
fi

# ==============================================================================
# MULTI-INSTANCE MODE (WARP_INSTANCES > 1)
#
# Each warp-svc uses STATE_DIRECTORY and RUNTIME_DIRECTORY env vars
# (systemd convention) to see its own data dir and IPC socket.
# Current egress IPs are shared/dynamic Cloudflare addresses.
#
# No extra Docker capabilities required (no SYS_ADMIN).
# ==============================================================================

echo "========================================"
echo " Multi-Instance WARP Mode"
echo " Instances : ${WARP_INSTANCES}"
if [ "$ZT_MODE" = true ]; then
echo " Enrollment : Zero Trust (${WARP_ORG})"
fi
if [ "$PROXY_MODE" = "dedicated" ]; then
echo " Proxy mode : dedicated (1 port per instance, base: ${PROXY_BASE_PORT})"
else
echo " Proxy mode : round-robin"
fi
if [ "$NUM_KEYS" -gt 0 ]; then
echo " License keys : ${NUM_KEYS} (auto-fallback)"
fi
echo "========================================"
echo ""

# ---- start each WARP instance with isolated paths ----
INSTANCE_PIDS=()
for i in $(seq 0 $((WARP_INSTANCES - 1))); do
    PORT=$((40000 + i))
    /start-warp-instance.sh \
        "$i" "$PORT" "$LICENSE_KEYS_CSV" "${WARP_CONNECT_TIMEOUT:-30}" &
    INSTANCE_PIDS+=($!)
    sleep $((5 + RANDOM % 5))  # stagger with jitter (5-9s) to avoid Cloudflare API rate-limiting
done

# ---- verify each instance is connected to WARP (parallel) ----
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
            if curl -s --connect-timeout 3 --socks5 "127.0.0.1:${PORT}" \
                "https://cloudflare.com/cdn-cgi/trace" 2>/dev/null | grep -qE 'warp=(on|plus)'; then
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
    echo "Error: no WARP instances started successfully. Exiting."
    exit 1
fi

# ---- generate GOST config (only include verified instances) ----
if [ "$PROXY_MODE" = "dedicated" ]; then
    generate_gost_config_dedicated "$VERIFY_DIR"
else
    generate_gost_config_roundrobin "$VERIFY_DIR"
fi
rm -rf "$VERIFY_DIR"

# ---- summary ----
echo ""
if [ "$PROXY_MODE" = "dedicated" ]; then
    echo "=== Proxy Endpoints (dedicated, ${READY_COUNT} instances) ==="
    for i in $(seq 0 $((WARP_INSTANCES - 1))); do
        DPORT=$((PROXY_BASE_PORT + i))
        echo "  SOCKS5 instance $((i+1)) : :${DPORT} -> WARP $((i+1))"
    done
    echo "  ---"
    echo "  SOCKS5 (Direct): :1081"
    echo "  HTTP   (Direct): :8081"
    echo "  SS     (Direct): :8389"
    if [ -n "$PROXY_USER" ]; then
        echo "  Auth: ${PROXY_USER}:***"
    fi
    echo "========================================================="
else
    echo "=== Proxy Endpoints (round-robin across ${READY_COUNT} instances) ==="
    echo "  SOCKS5 (WARP)  : :1080"
    echo "  HTTP   (WARP)  : :8080"
    echo "  SS     (WARP)  : :8388"
    echo "  SOCKS5 (Direct): :1081"
    echo "  HTTP   (Direct): :8081"
    echo "  SS     (Direct): :8389"
    if [ -n "$PROXY_USER" ]; then
        echo "  Auth: ${PROXY_USER}:***"
    fi
    echo "========================================================="
fi
echo ""

# ---- cleanup on shutdown ----
cleanup() {
    echo "Shutting down ${WARP_INSTANCES} WARP instances..."
    # Deregister devices so they don't count against the WARP+ per-key limit
    # (or Zero Trust's 50-device limit). Without this, each container recreation
    # would leave orphaned device registrations on Cloudflare's side.
    for i in $(seq 0 $((WARP_INSTANCES - 1))); do
        local run="/run/warp-${i}"
        local dbus="/run/dbus-${i}/system_bus_socket"
        sudo env RUNTIME_DIRECTORY="$run" DBUS_SYSTEM_BUS_ADDRESS="unix:path=${dbus}" \
            warp-cli --accept-tos registration delete 2>/dev/null || true
    done
    for pid in "${INSTANCE_PIDS[@]}"; do
        sudo kill "$pid" 2>/dev/null || true
    done
    kill "$ADMIN_PID" 2>/dev/null || true
    kill "$GOST_PID" 2>/dev/null || true
    kill "$WATCHDOG_PID" 2>/dev/null || true
    wait
}
trap cleanup SIGTERM SIGINT

# ---- start optional admin panel ----

# ---- start watchdog (multi-instance only) ----
WATCHDOG_PID=""
if [ "$WARP_INSTANCES" -gt 1 ] && [ "${WARP_WATCHDOG_ENABLED:-true}" = "true" ]; then
    echo "Starting watchdog for ${WARP_INSTANCES} instances..."
    /watchdog.sh &
    WATCHDOG_PID=$!
elif [ "$WARP_INSTANCES" -gt 1 ]; then
    echo "Watchdog disabled (WARP_WATCHDOG_ENABLED=false)"
fi

# ---- start optional admin panel ----
ADMIN_PID=""
if [ "${ADMIN_ENABLED:-false}" = "true" ]; then
    echo "Starting admin panel on :${ADMIN_PORT:-9090}"
    python3 /admin/server.py &
    ADMIN_PID=$!
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
