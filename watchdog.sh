#!/bin/bash

# WARP Instance Watchdog
# Monitors each WARP instance and attempts recovery when connectivity is lost.
# Launched by entrypoint.sh as a background process.
#
# State is written to /tmp/watchdog-state.json for the admin panel to read.
# Uses per-instance flock files at /tmp/watchdog-lock-N to prevent concurrent
# recovery from the watchdog and the admin API.

set -euo pipefail

WARP_INSTANCES=${WARP_INSTANCES:-1}
WARP_WATCHDOG_ENABLED=${WARP_WATCHDOG_ENABLED:-true}
WARP_WATCHDOG_INTERVAL=${WARP_WATCHDOG_INTERVAL:-30}
WARP_WATCHDOG_FAILURE_THRESHOLD=${WARP_WATCHDOG_FAILURE_THRESHOLD:-3}
WARP_WATCHDOG_RECOVERY_TIMEOUT=${WARP_WATCHDOG_RECOVERY_TIMEOUT:-30}
WARP_WATCHDOG_RESTART_COOLDOWN=${WARP_WATCHDOG_RESTART_COOLDOWN:-120}

WATCHDOG_STATE_FILE="/tmp/watchdog-state.json"
PROXY_MODE=${PROXY_MODE:-round-robin}
PROXY_BASE_PORT=${PROXY_BASE_PORT:-2080}

# Per-instance runtime state (bash arrays)
declare -A WD_STATUS            # healthy|degraded|recovering|offline
declare -A WD_CONSECUTIVE_FAILS
declare -A WD_LAST_CHECK
declare -A WD_LAST_SUCCESS
declare -A WD_LAST_FAILURE
declare -A WD_LAST_RECONNECT
declare -A WD_LAST_RESTART
declare -A WD_RECONNECT_COUNT
declare -A WD_RESTART_COUNT
declare -A WD_RECOVERY_STATUS   # none|reconnecting|restarting
declare -A WD_LAST_ERROR
declare -A WD_PREV_EGRESS
declare -A WD_CURRENT_EGRESS
declare -A WD_LAST_EGRESS_CHANGE

now_iso() {
    date -u +"%Y-%m-%dT%H:%M:%SZ"
}

now_epoch() {
    date +%s
}

log() {
    echo "[watchdog] $*"
}

# Initialize state for all instances
init_state() {
    for i in $(seq 0 $((WARP_INSTANCES - 1))); do
        WD_STATUS[$i]="healthy"
        WD_CONSECUTIVE_FAILS[$i]=0
        WD_LAST_CHECK[$i]=""
        WD_LAST_SUCCESS[$i]=""
        WD_LAST_FAILURE[$i]=""
        WD_LAST_RECONNECT[$i]=""
        WD_LAST_RESTART[$i]=""
        WD_RECONNECT_COUNT[$i]=0
        WD_RESTART_COUNT[$i]=0
        WD_RECOVERY_STATUS[$i]="none"
        WD_LAST_ERROR[$i]=""
        WD_PREV_EGRESS[$i]=""
        WD_CURRENT_EGRESS[$i]=""
        WD_LAST_EGRESS_CHANGE[$i]=""
    done
}

# Write state to JSON file for the admin panel
write_state() {
    local tmp
    tmp=$(mktemp /tmp/watchdog-state.XXXXXX)
    {
        echo "{"
        echo "  \"updated\": \"$(now_iso)\","
        echo "  \"enabled\": ${WARP_WATCHDOG_ENABLED},"
        echo "  \"interval\": ${WARP_WATCHDOG_INTERVAL},"
        echo "  \"failure_threshold\": ${WARP_WATCHDOG_FAILURE_THRESHOLD},"
        echo "  \"recovery_timeout\": ${WARP_WATCHDOG_RECOVERY_TIMEOUT},"
        echo "  \"restart_cooldown\": ${WARP_WATCHDOG_RESTART_COOLDOWN},"
        echo "  \"instances\": {"
        local first=true
        for i in $(seq 0 $((WARP_INSTANCES - 1))); do
            if [ "$first" = true ]; then
                first=false
            else
                echo ","
            fi
            cat <<INST
    "$i": {
      "status": "${WD_STATUS[$i]}",
      "consecutive_failures": ${WD_CONSECUTIVE_FAILS[$i]},
      "last_check": "${WD_LAST_CHECK[$i]}",
      "last_success": "${WD_LAST_SUCCESS[$i]}",
      "last_failure": "${WD_LAST_FAILURE[$i]}",
      "last_reconnect": "${WD_LAST_RECONNECT[$i]}",
      "last_restart": "${WD_LAST_RESTART[$i]}",
      "reconnect_count": ${WD_RECONNECT_COUNT[$i]},
      "restart_count": ${WD_RESTART_COUNT[$i]},
      "recovery_status": "${WD_RECOVERY_STATUS[$i]}",
      "last_error": "${WD_LAST_ERROR[$i]}",
      "previous_egress": "${WD_PREV_EGRESS[$i]}",
      "current_egress": "${WD_CURRENT_EGRESS[$i]}",
      "last_egress_change": "${WD_LAST_EGRESS_CHANGE[$i]}"
    }
INST
        done
        echo ""
        echo "  }"
        echo "}"
    } > "$tmp"
    mv "$tmp" "$WATCHDOG_STATE_FILE"
}

# Check instance health via its internal SOCKS5 proxy
# Returns 0 if healthy, 1 if unhealthy
# Sets HEALTH_CHECK_EGRESS as side-effect
HEALTH_CHECK_EGRESS=""
check_instance_health() {
    local instance=$1
    local port=$((40000 + instance))
    local trace_output

    HEALTH_CHECK_EGRESS=""

    trace_output=$(curl \
        --socks5-hostname "127.0.0.1:${port}" \
        --max-time 10 \
        -s \
        "https://www.cloudflare.com/cdn-cgi/trace" 2>/dev/null) || return 1

    if echo "$trace_output" | grep -qE '^warp=(on|plus)'; then
        # Extract egress IP
        HEALTH_CHECK_EGRESS=$(echo "$trace_output" | grep '^ip=' | cut -d= -f2)
        return 0
    fi
    return 1
}

# Run warp-cli for a specific instance
instance_wcli() {
    local instance=$1
    shift
    local run_dir="/run/warp-${instance}"
    local dbus_sock="/run/dbus-${instance}/system_bus_socket"
    sudo env \
        RUNTIME_DIRECTORY="$run_dir" \
        DBUS_SYSTEM_BUS_ADDRESS="unix:path=${dbus_sock}" \
        warp-cli --accept-tos "$@"
}

# Attempt a light reconnect for a specific instance
try_reconnect() {
    local instance=$1
    log "instance ${instance} reconnect requested"
    WD_RECOVERY_STATUS[$instance]="reconnecting"
    WD_LAST_RECONNECT[$instance]=$(now_iso)
    WD_RECONNECT_COUNT[$instance]=$(( ${WD_RECONNECT_COUNT[$instance]} + 1 ))
    write_state

    # Execute reconnect
    instance_wcli "$instance" connect 2>/dev/null || true

    # Wait for recovery
    local elapsed=0
    while [ "$elapsed" -lt "$WARP_WATCHDOG_RECOVERY_TIMEOUT" ]; do
        sleep 3
        elapsed=$((elapsed + 3))
        if check_instance_health "$instance"; then
            log "instance ${instance} reconnect successful"
            WD_STATUS[$instance]="healthy"
            WD_CONSECUTIVE_FAILS[$instance]=0
            WD_RECOVERY_STATUS[$instance]="none"
            WD_LAST_SUCCESS[$instance]=$(now_iso)
            WD_LAST_ERROR[$instance]=""
            update_egress "$instance"
            write_state
            return 0
        fi
    done

    log "instance ${instance} reconnect failed"
    return 1
}

# Stop only the warp-svc for a specific instance
stop_instance_warp() {
    local instance=$1
    local pid_file="/tmp/warp-instance-${instance}.pid"

    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file" 2>/dev/null || true)
        if [ -n "$pid" ]; then
            sudo kill "$pid" 2>/dev/null || true
            # Wait briefly for clean shutdown
            local wait=0
            while [ "$wait" -lt 10 ] && sudo kill -0 "$pid" 2>/dev/null; do
                sleep 1
                wait=$((wait + 1))
            done
            # Force kill if still alive
            sudo kill -9 "$pid" 2>/dev/null || true
        fi
    fi

    # Also kill by matching environment pattern
    sudo pkill -f "STATE_DIRECTORY=/var/lib/cloudflare-warp/instance-${instance}[^0-9]" 2>/dev/null || true
    sudo pkill -f "STATE_DIRECTORY=/var/lib/cloudflare-warp/instance-${instance}$" 2>/dev/null || true
    sleep 1
}

# Restart only the warp-svc for a specific instance, preserving state
restart_instance_warp() {
    local instance=$1
    local port=$((40000 + instance))
    local data_dir="/var/lib/cloudflare-warp/instance-${instance}"
    local run_dir="/run/warp-${instance}"
    local dbus_dir="/run/dbus-${instance}"
    local dbus_sock="${dbus_dir}/system_bus_socket"
    local pid_file="/tmp/warp-instance-${instance}.pid"

    log "instance ${instance} restarting warp-svc"
    WD_RECOVERY_STATUS[$instance]="restarting"
    WD_LAST_RESTART[$instance]=$(now_iso)
    WD_RESTART_COUNT[$instance]=$(( ${WD_RESTART_COUNT[$instance]} + 1 ))
    write_state

    # Stop the existing warp-svc
    stop_instance_warp "$instance"

    # Ensure directories still exist
    sudo mkdir -p "$data_dir" "$run_dir" "$dbus_dir"

    # Check if D-Bus daemon is still running, restart if needed
    if [ ! -S "$dbus_sock" ]; then
        log "instance ${instance} restarting D-Bus daemon"
        sudo dbus-daemon \
            --address="unix:path=${dbus_sock}" \
            --config-file=/usr/share/dbus-1/system.conf \
            --nopidfile --nofork >/dev/null 2>&1 &
        sleep 1
    fi

    # Start warp-svc with same paths (preserves reg.json and state)
    sudo env \
        STATE_DIRECTORY="$data_dir" \
        RUNTIME_DIRECTORY="$run_dir" \
        DBUS_SYSTEM_BUS_ADDRESS="unix:path=${dbus_sock}" \
        warp-svc --accept-tos &
    local new_pid=$!
    echo "$new_pid" > "$pid_file"

    log "instance ${instance} warp-svc restarted (PID: ${new_pid})"

    # Wait for the daemon to become ready
    local elapsed=0
    while [ "$elapsed" -lt "$WARP_WATCHDOG_RECOVERY_TIMEOUT" ]; do
        if instance_wcli "$instance" status 2>/dev/null | grep -qE '(Status|Connected)'; then
            break
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done

    # Reconnect (warp-svc needs connect after restart)
    instance_wcli "$instance" mode proxy 2>/dev/null || true
    instance_wcli "$instance" proxy port "$port" 2>/dev/null || true
    instance_wcli "$instance" connect 2>/dev/null || true

    # Wait and verify
    elapsed=0
    while [ "$elapsed" -lt "$WARP_WATCHDOG_RECOVERY_TIMEOUT" ]; do
        sleep 3
        elapsed=$((elapsed + 3))
        if check_instance_health "$instance"; then
            log "instance ${instance} healthy again"
            WD_STATUS[$instance]="healthy"
            WD_CONSECUTIVE_FAILS[$instance]=0
            WD_RECOVERY_STATUS[$instance]="none"
            WD_LAST_SUCCESS[$instance]=$(now_iso)
            WD_LAST_ERROR[$instance]=""
            update_egress "$instance"
            write_state
            return 0
        fi
    done

    log "instance ${instance} restart did not restore health"
    WD_STATUS[$instance]="offline"
    WD_RECOVERY_STATUS[$instance]="none"
    WD_LAST_ERROR[$instance]="restart failed to restore connectivity"
    write_state
    return 1
}

# Update egress IP tracking after recovery
update_egress() {
    local instance=$1
    local new_egress="$HEALTH_CHECK_EGRESS"

    if [ -n "$new_egress" ]; then
        local old_egress="${WD_CURRENT_EGRESS[$instance]}"
        if [ -n "$old_egress" ] && [ "$old_egress" != "$new_egress" ]; then
            WD_PREV_EGRESS[$instance]="$old_egress"
            WD_LAST_EGRESS_CHANGE[$instance]=$(now_iso)
            log "instance ${instance} egress changed: ${old_egress} -> ${new_egress}"
        fi
        WD_CURRENT_EGRESS[$instance]="$new_egress"
    fi
}

# Check cooldown for restart
in_restart_cooldown() {
    local instance=$1
    local last_restart="${WD_LAST_RESTART[$instance]}"
    if [ -z "$last_restart" ]; then
        return 1  # Not in cooldown
    fi
    local last_ts
    last_ts=$(date -d "$last_restart" +%s 2>/dev/null || echo 0)
    local now
    now=$(now_epoch)
    local diff=$((now - last_ts))
    if [ "$diff" -lt "$WARP_WATCHDOG_RESTART_COOLDOWN" ]; then
        return 0  # In cooldown
    fi
    return 1
}

# Process a single instance check cycle
process_instance() {
    local instance=$1
    local lockfile="/tmp/watchdog-lock-${instance}"

    # Skip if already recovering
    if [ "${WD_RECOVERY_STATUS[$instance]}" != "none" ]; then
        return
    fi

    WD_LAST_CHECK[$instance]=$(now_iso)

    if check_instance_health "$instance"; then
        # Healthy
        if [ "${WD_CONSECUTIVE_FAILS[$instance]}" -gt 0 ]; then
            log "instance ${instance} recovered (was at ${WD_CONSECUTIVE_FAILS[$instance]} consecutive failures)"
        fi
        WD_CONSECUTIVE_FAILS[$instance]=0
        WD_STATUS[$instance]="healthy"
        WD_LAST_SUCCESS[$instance]=$(now_iso)
        WD_LAST_ERROR[$instance]=""
        update_egress "$instance"
    else
        # Failed
        WD_CONSECUTIVE_FAILS[$instance]=$(( ${WD_CONSECUTIVE_FAILS[$instance]} + 1 ))
        WD_LAST_FAILURE[$instance]=$(now_iso)
        local fails=${WD_CONSECUTIVE_FAILS[$instance]}

        log "instance ${instance} check failed (${fails}/${WARP_WATCHDOG_FAILURE_THRESHOLD})"

        if [ "$fails" -lt "$WARP_WATCHDOG_FAILURE_THRESHOLD" ]; then
            WD_STATUS[$instance]="degraded"
        else
            # Threshold reached - enter recovery
            log "instance ${instance} entering recovery"
            WD_STATUS[$instance]="recovering"

            # Acquire per-instance lock (non-blocking)
            (
                flock -n 200 || {
                    log "instance ${instance} recovery skipped (lock held)"
                    return
                }

                # Step 1: Try reconnect
                if try_reconnect "$instance"; then
                    return
                fi

                # Step 2: Full restart if not in cooldown
                if in_restart_cooldown "$instance"; then
                    log "instance ${instance} in restart cooldown, skipping restart"
                    WD_STATUS[$instance]="offline"
                    WD_RECOVERY_STATUS[$instance]="none"
                    WD_LAST_ERROR[$instance]="in restart cooldown"
                    return
                fi

                restart_instance_warp "$instance"

            ) 200>"$lockfile"
        fi
    fi
    write_state
}

# Main loop
main() {
    if [ "$WARP_WATCHDOG_ENABLED" != "true" ]; then
        log "watchdog disabled"
        # Write an initial disabled state
        WARP_INSTANCES=${WARP_INSTANCES} write_state
        exit 0
    fi

    log "starting (instances=${WARP_INSTANCES}, interval=${WARP_WATCHDOG_INTERVAL}s, threshold=${WARP_WATCHDOG_FAILURE_THRESHOLD}, recovery_timeout=${WARP_WATCHDOG_RECOVERY_TIMEOUT}s, cooldown=${WARP_WATCHDOG_RESTART_COOLDOWN}s)"

    init_state
    write_state

    # Initial grace period - let instances stabilize
    sleep "$WARP_WATCHDOG_INTERVAL"

    while true; do
        for i in $(seq 0 $((WARP_INSTANCES - 1))); do
            process_instance "$i"
        done
        sleep "$WARP_WATCHDOG_INTERVAL"
    done
}

main "$@"
