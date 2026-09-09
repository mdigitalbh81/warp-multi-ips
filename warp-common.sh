#!/bin/bash

set -e

WARP_DATA_DIR=${WARP_DATA_DIR:-/var/lib/cloudflare-warp}
ADMIN_CONFIG_FILE=${ADMIN_CONFIG_FILE:-${WARP_DATA_DIR}/admin-config.json}
GOST_CONFIG_FILE=${GOST_CONFIG_FILE:-/tmp/gost-config.yaml}
HEALTHY_PORTS_FILE=${HEALTHY_PORTS_FILE:-/tmp/healthy-warp-ports}
WARP_ENV_FILE=${WARP_ENV_FILE:-/tmp/warp-admin-env}
MAX_WARP_INSTANCES=${MAX_WARP_INSTANCES:-45}
export MAX_WARP_INSTANCES
PROXY_LOG_LEVEL=${PROXY_LOG_LEVEL:-warn}
export PROXY_LOG_LEVEL
export GOST_LOGGER_LEVEL="${PROXY_LOG_LEVEL}"
WARP_LOG_LEVEL=${WARP_LOG_LEVEL:-warn}
export WARP_LOG_LEVEL
NORMAL_SHUTDOWN_DEREGISTERS=${NORMAL_SHUTDOWN_DEREGISTERS:-false}
export NORMAL_SHUTDOWN_DEREGISTERS

filter_warp_logs() {
    local level="${WARP_LOG_LEVEL:-warn}"
    level=$(echo "$level" | tr '[:upper:]' '[:lower:]')
    if [ "$level" = "debug" ]; then
        cat
        return
    fi
    awk -v lvl="$level" '
    {
        line = $0
		clean = line
		gsub(/\033\[[0-9;?]*[ -/]*[@-~]/, "", clean)
		gsub(/\033\][^\007\033]*(\007|\033\\)/, "", clean)
		gsub(/\033[()][A-Za-z0-9]/, "", clean)
		gsub(/\r/, "", clean)
		low = tolower(clean)
        if (low ~ /socks greeting failed/ && (low ~ /unexpected ?eof/ || low ~ /unexpectedeof/)) { next }
        is_err = (clean ~ /(^|[[:space:]\[])(ERROR|FATAL)([[:space:]\]:]|$)/ || clean ~ /level=(error|fatal)/ || low ~ /(^|[[:space:]])panic(:|[[:space:]]|$)/)
		is_warn = (clean ~ /(^|[[:space:]\[])WARN([[:space:]\]:]|$)/ || clean ~ /level=warn/)
		if (lvl == "error") {
			if (is_err) { print line; fflush() }
			next
		}
		if (is_err || is_warn) {
			print line; fflush()
			next
		}
		if (low ~ /(masquetunnelstatsupdated|warp-network-health-stats|tunnel_stats_reporting_task|warp-connection-stats)/) { next }
		if (clean ~ /(^|[[:space:]\[])(DEBUG|TRACE)([[:space:]\]:]|$)/) { next }
		if (clean ~ /(^|[[:space:]\[])INFO([[:space:]\]:]|$)/) {
			if (low ~ /(connect|reconnect|disconnect|register|login|auth|fail|lost|loss|restart|shutdown)/) { print line; fflush() }
			next
		}
		print line; fflush()
	    }'
}
export -f filter_warp_logs 2>/dev/null || true

write_file() {
    local file="$1"
    if { [ -d "$(dirname "$file")" ] && [ -w "$(dirname "$file")" ]; } || { [ -e "$file" ] && [ -w "$file" ]; }; then
        cat > "$file"
    else
        sudo tee "$file" >/dev/null
    fi
}

sync_admin_config() {
    if [ "${ADMIN_ENABLED:-false}" != "true" ] || [ ! -f "$ADMIN_CONFIG_FILE" ]; then
        return 0
    fi
    ADMIN_CONFIG_FILE="$ADMIN_CONFIG_FILE" \
    SYNC_INSTANCES="${ENV_WARP_INSTANCES_SET:+${WARP_INSTANCES}}" \
    SYNC_MODE="${ENV_PROXY_MODE_SET:+${PROXY_MODE}}" \
    SYNC_PORT="${ENV_PROXY_BASE_PORT_SET:+${PROXY_BASE_PORT}}" \
    SYNC_RPS="${ENV_PROXY_MAX_RPS_SET:+${PROXY_MAX_RPS}}" \
    SYNC_TIMEOUT="${ENV_WARP_CONNECT_TIMEOUT_SET:+${WARP_CONNECT_TIMEOUT}}" \
    SYNC_INTERVAL="${ENV_AUTO_REFRESH_INTERVAL_SET:+${AUTO_REFRESH_INTERVAL}}" \
    SYNC_HOST="${ENV_PROXY_HOST_OMNIROUTE_SET:+${PROXY_HOST_OMNIROUTE}}" \
    python3 -c '
import json, os, sys, tempfile

path = os.environ.get("ADMIN_CONFIG_FILE")
if not path or not os.path.isfile(path):
    sys.exit(0)

try:
    with open(path, "r") as f:
        data = json.load(f)
except Exception:
    sys.exit(0)

changed = False

def update_int(key, env_var):
    global changed
    val = os.environ.get(env_var)
    if val:
        try:
            int_val = int(val)
            if data.get(key) != int_val:
                data[key] = int_val
                changed = True
        except ValueError:
            pass

def update_str(key, env_var):
    global changed
    val = os.environ.get(env_var)
    if val is not None and val != "":
        if data.get(key) != val:
            data[key] = val
            changed = True

update_int("instances", "SYNC_INSTANCES")
update_str("proxy_mode", "SYNC_MODE")
update_int("proxy_base_port", "SYNC_PORT")
update_int("proxy_max_rps", "SYNC_RPS")
update_int("warp_connect_timeout", "SYNC_TIMEOUT")
update_int("auto_refresh_interval", "SYNC_INTERVAL")
update_str("proxy_host_omniroute", "SYNC_HOST")

if changed:
    dirname = os.path.dirname(path) or "."
    try:
        fd, tmp = tempfile.mkstemp(dir=dirname, prefix=".admin-cfg-sync.")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except (PermissionError, OSError):
        import subprocess
        subprocess.run(["sudo", "tee", path], input=json.dumps(data, indent=2) + "\n", text=True, capture_output=True)
' 2>/dev/null || true
}

load_admin_config(){
    local env_instances_set="${ENV_WARP_INSTANCES:-${WARP_INSTANCES+x}}"
    local env_instances_val="${ENV_WARP_INSTANCES_VALUE:-${WARP_INSTANCES:-}}"
    local env_mode_set="${ENV_PROXY_MODE:-${PROXY_MODE+x}}"
    local env_mode_val="${ENV_PROXY_MODE_VALUE:-${PROXY_MODE:-}}"
    local env_port_set="${ENV_PROXY_BASE_PORT:-${PROXY_BASE_PORT+x}}"
    local env_port_val="${ENV_PROXY_BASE_PORT_VALUE:-${PROXY_BASE_PORT:-}}"
    local env_rps_set="${ENV_PROXY_MAX_RPS:-${PROXY_MAX_RPS+x}}"
    local env_rps_val="${ENV_PROXY_MAX_RPS_VALUE:-${PROXY_MAX_RPS:-}}"
    local env_timeout_set="${ENV_WARP_CONNECT_TIMEOUT:-${WARP_CONNECT_TIMEOUT+x}}"
    local env_timeout_val="${ENV_WARP_CONNECT_TIMEOUT_VALUE:-${WARP_CONNECT_TIMEOUT:-}}"
    local env_interval_set="${ENV_AUTO_REFRESH_INTERVAL:-${AUTO_REFRESH_INTERVAL+x}}"
    local env_interval_val="${ENV_AUTO_REFRESH_INTERVAL_VALUE:-${AUTO_REFRESH_INTERVAL:-}}"
    local env_host_val="${ENV_PROXY_HOST_OMNIROUTE:-${PROXY_HOST_OMNIROUTE:-}}"

    if [ "${ADMIN_ENABLED:-false}" = "true" ] && [ -f "$ADMIN_CONFIG_FILE" ]; then
        local persisted_instances persisted_mode persisted_port persisted_host
        local persisted_rps persisted_timeout persisted_interval

        persisted_instances=$(jq -r '.instances // ""' "$ADMIN_CONFIG_FILE")
        persisted_mode=$(jq -r '.proxy_mode // ""' "$ADMIN_CONFIG_FILE")
        persisted_port=$(jq -r '.proxy_base_port // ""' "$ADMIN_CONFIG_FILE")
        persisted_host=$(jq -r '.proxy_host_omniroute // ""' "$ADMIN_CONFIG_FILE")
        persisted_rps=$(jq -r '.proxy_max_rps // ""' "$ADMIN_CONFIG_FILE")
        persisted_timeout=$(jq -r '.warp_connect_timeout // ""' "$ADMIN_CONFIG_FILE")
        persisted_interval=$(jq -r '.auto_refresh_interval // ""' "$ADMIN_CONFIG_FILE")
        PROXY_AUTH_ENABLED=$(jq -r '.proxy_auth_enabled // false' "$ADMIN_CONFIG_FILE")
        CFG_PROXY_USER=$(jq -r '.proxy_user // ""' "$ADMIN_CONFIG_FILE")
        CFG_PROXY_PASS=$(jq -r '.proxy_password // ""' "$ADMIN_CONFIG_FILE")

        if [ -n "$env_instances_set" ] && [ -n "$env_instances_val" ]; then
            WARP_INSTANCES="$env_instances_val"
            ENV_WARP_INSTANCES_SET="true"
        elif [ -n "$persisted_instances" ]; then
            WARP_INSTANCES="$persisted_instances"
            ENV_WARP_INSTANCES_SET=""
        else
    WARP_INSTANCES="${WARP_INSTANCES:-1}"
    ENV_WARP_INSTANCES_SET=""
  fi
  export ENV_WARP_INSTANCES_SET
  export WARP_INSTANCES

  if [ -n "$env_mode_set" ] && [ -n "$env_mode_val" ]; then
            PROXY_MODE="$env_mode_val"
            ENV_PROXY_MODE_SET="true"
        elif [ -n "$persisted_mode" ]; then
            PROXY_MODE="$persisted_mode"
            ENV_PROXY_MODE_SET=""
        else
            PROXY_MODE="${PROXY_MODE:-round-robin}"
            ENV_PROXY_MODE_SET=""
        fi

        if [ -n "$env_port_set" ] && [ -n "$env_port_val" ]; then
            PROXY_BASE_PORT="$env_port_val"
            ENV_PROXY_BASE_PORT_SET="true"
        elif [ -n "$persisted_port" ]; then
            PROXY_BASE_PORT="$persisted_port"
            ENV_PROXY_BASE_PORT_SET=""
        else
            PROXY_BASE_PORT="${PROXY_BASE_PORT:-2080}"
            ENV_PROXY_BASE_PORT_SET=""
        fi

        if [ -n "$env_host_val" ]; then
            PROXY_HOST_OMNIROUTE="$env_host_val"
            ENV_PROXY_HOST_OMNIROUTE_SET="true"
        elif [ -n "$persisted_host" ]; then
            PROXY_HOST_OMNIROUTE="$persisted_host"
            ENV_PROXY_HOST_OMNIROUTE_SET=""
        elif [ -n "${PROXY_HOST:-}" ]; then
            PROXY_HOST_OMNIROUTE="$PROXY_HOST"
            ENV_PROXY_HOST_OMNIROUTE_SET="true"
        else
            PROXY_HOST_OMNIROUTE=""
            ENV_PROXY_HOST_OMNIROUTE_SET=""
        fi

        if [ -n "$env_rps_set" ] && [ -n "$env_rps_val" ]; then
            PROXY_MAX_RPS="$env_rps_val"
            ENV_PROXY_MAX_RPS_SET="true"
        elif [ -n "$persisted_rps" ]; then
            PROXY_MAX_RPS="$persisted_rps"
            ENV_PROXY_MAX_RPS_SET=""
        else
            PROXY_MAX_RPS="${PROXY_MAX_RPS:-50}"
            ENV_PROXY_MAX_RPS_SET=""
        fi

        if [ -n "$env_timeout_set" ] && [ -n "$env_timeout_val" ]; then
            WARP_CONNECT_TIMEOUT="$env_timeout_val"
            ENV_WARP_CONNECT_TIMEOUT_SET="true"
        elif [ -n "$persisted_timeout" ]; then
            WARP_CONNECT_TIMEOUT="$persisted_timeout"
            ENV_WARP_CONNECT_TIMEOUT_SET=""
        else
            WARP_CONNECT_TIMEOUT="${WARP_CONNECT_TIMEOUT:-30}"
            ENV_WARP_CONNECT_TIMEOUT_SET=""
        fi

        if [ -n "$env_interval_set" ] && [ -n "$env_interval_val" ]; then
            AUTO_REFRESH_INTERVAL="$env_interval_val"
            ENV_AUTO_REFRESH_INTERVAL_SET="true"
        elif [ -n "$persisted_interval" ]; then
            AUTO_REFRESH_INTERVAL="$persisted_interval"
            ENV_AUTO_REFRESH_INTERVAL_SET=""
        else
            AUTO_REFRESH_INTERVAL="${AUTO_REFRESH_INTERVAL:-60}"
            ENV_AUTO_REFRESH_INTERVAL_SET=""
        fi

        if [ "$PROXY_AUTH_ENABLED" = "true" ]; then
            PROXY_USER="$CFG_PROXY_USER"
            PROXY_PASS="$CFG_PROXY_PASS"
        else
            PROXY_USER=""
            PROXY_PASS=""
        fi

        sync_admin_config
    else
        if [ -n "$env_instances_set" ] && [ -n "$env_instances_val" ]; then
            WARP_INSTANCES="$env_instances_val"
            ENV_WARP_INSTANCES_SET="true"
        else
    WARP_INSTANCES="${WARP_INSTANCES:-1}"
    ENV_WARP_INSTANCES_SET=""
  fi
  export ENV_WARP_INSTANCES_SET
  export WARP_INSTANCES

  if [ -n "$env_mode_set" ] && [ -n "$env_mode_val" ]; then
            PROXY_MODE="$env_mode_val"
        else
            PROXY_MODE="${PROXY_MODE:-round-robin}"
        fi

        if [ -n "$env_port_set" ] && [ -n "$env_port_val" ]; then
            PROXY_BASE_PORT="$env_port_val"
        else
            PROXY_BASE_PORT="${PROXY_BASE_PORT:-2080}"
        fi

        if [ -n "$env_rps_set" ] && [ -n "$env_rps_val" ]; then
            PROXY_MAX_RPS="$env_rps_val"
        else
            PROXY_MAX_RPS="${PROXY_MAX_RPS:-50}"
        fi

        if [ -n "$env_timeout_set" ] && [ -n "$env_timeout_val" ]; then
            WARP_CONNECT_TIMEOUT="$env_timeout_val"
        else
            WARP_CONNECT_TIMEOUT="${WARP_CONNECT_TIMEOUT:-30}"
        fi

        if [ -n "$env_interval_set" ] && [ -n "$env_interval_val" ]; then
            AUTO_REFRESH_INTERVAL="$env_interval_val"
        else
            AUTO_REFRESH_INTERVAL="${AUTO_REFRESH_INTERVAL:-60}"
        fi

        if [ -n "$env_host_val" ]; then
            PROXY_HOST_OMNIROUTE="$env_host_val"
        elif [ -n "${PROXY_HOST:-}" ]; then
            PROXY_HOST_OMNIROUTE="$PROXY_HOST"
        else
            PROXY_HOST_OMNIROUTE=""
        fi
    fi
}

init_admin_config() {
    if [ "${ADMIN_ENABLED:-false}" != "true" ]; then
        return 0
    fi
    mkdir -p "$(dirname "$ADMIN_CONFIG_FILE")" 2>/dev/null || sudo mkdir -p "$(dirname "$ADMIN_CONFIG_FILE")"
    if [ ! -f "$ADMIN_CONFIG_FILE" ]; then
        local auth_enabled=false
        if [ -n "${PROXY_USER:-}" ] && [ -n "${PROXY_PASS:-}" ]; then
            auth_enabled=true
        fi
        jq -n \
            --argjson instances "${WARP_INSTANCES:-1}" \
            --arg proxy_mode "${PROXY_MODE:-round-robin}" \
            --argjson proxy_base_port "${PROXY_BASE_PORT:-2080}" \
            --argjson proxy_max_rps "${PROXY_MAX_RPS:-50}" \
            --argjson warp_connect_timeout "${WARP_CONNECT_TIMEOUT:-30}" \
            --argjson auto_refresh_interval "${AUTO_REFRESH_INTERVAL:-60}" \
            --argjson proxy_auth_enabled "$auth_enabled" \
            --arg proxy_user "${PROXY_USER:-}" \
            --arg proxy_password "${PROXY_PASS:-}" \
            --arg proxy_host_omniroute "${PROXY_HOST_OMNIROUTE:-${PROXY_HOST:-}}" \
            '{
              instances: $instances,
              proxy_mode: $proxy_mode,
              proxy_base_port: $proxy_base_port,
              proxy_host_omniroute: $proxy_host_omniroute,
              proxy_max_rps: $proxy_max_rps,
              warp_connect_timeout: $warp_connect_timeout,
              auto_refresh_interval: $auto_refresh_interval,
              proxy_auth_enabled: $proxy_auth_enabled,
              proxy_user: $proxy_user,
              proxy_password: $proxy_password
            }' | write_file "$ADMIN_CONFIG_FILE"
        echo "Admin config initialized at ${ADMIN_CONFIG_FILE}"
    fi
}

validate_runtime_config() {
    if [ "$PROXY_MODE" != "round-robin" ] && [ "$PROXY_MODE" != "dedicated" ]; then
        echo "Error: PROXY_MODE must be 'round-robin' or 'dedicated' (got: '${PROXY_MODE}')"
        exit 1
    fi

    if ! [[ "$PROXY_BASE_PORT" =~ ^[0-9]+$ ]]; then
        echo "Error: PROXY_BASE_PORT must be a positive integer (got: '${PROXY_BASE_PORT}')"
        exit 1
    fi
    if [ "$PROXY_BASE_PORT" -lt 1 ] || [ "$PROXY_BASE_PORT" -gt 65535 ]; then
        echo "Error: PROXY_BASE_PORT must be between 1 and 65535 (got: ${PROXY_BASE_PORT})"
        exit 1
    fi

    if ! [[ "$WARP_INSTANCES" =~ ^[0-9]+$ ]] || [ "$WARP_INSTANCES" -lt 1 ] || [ "$WARP_INSTANCES" -gt "$MAX_WARP_INSTANCES" ]; then
        echo "Error: WARP_INSTANCES must be an integer between 1 and ${MAX_WARP_INSTANCES} (got: ${WARP_INSTANCES})"
        exit 1
    fi

    if [ "$PROXY_MODE" = "dedicated" ]; then
        LAST_PORT=$((PROXY_BASE_PORT + WARP_INSTANCES - 1))
        if [ "$LAST_PORT" -gt 65535 ]; then
            echo "Error: PROXY_BASE_PORT=${PROXY_BASE_PORT} with WARP_INSTANCES=${WARP_INSTANCES} would exceed TCP port range (last port: ${LAST_PORT})"
            exit 1
        fi

        FIXED_PORTS="1081 8080 8081 8388 8389 ${ADMIN_PORT:-9090}"
        for i in $(seq 0 $((WARP_INSTANCES - 1))); do
            DPORT=$((PROXY_BASE_PORT + i))
            for fp in $FIXED_PORTS; do
                if [ "$DPORT" -eq "$fp" ]; then
                    echo "Error: Dedicated port ${DPORT} (instance $((i+1))) conflicts with fixed service port ${fp}"
                    exit 1
                fi
            done
            for j in $(seq 0 $((WARP_INSTANCES - 1))); do
                WPORT=$((40000 + j))
                if [ "$DPORT" -eq "$WPORT" ]; then
                    echo "Error: Dedicated port ${DPORT} (instance $((i+1))) conflicts with internal WARP port ${WPORT}"
                    exit 1
                fi
            done
        done
    fi
}

write_admin_env_file() {
    {
        printf 'WARP_INSTANCES=%s\n' "$WARP_INSTANCES"
        printf 'PROXY_MODE=%s\n' "$PROXY_MODE"
        printf 'PROXY_BASE_PORT=%s\n' "$PROXY_BASE_PORT"
        printf 'PROXY_MAX_RPS=%s\n' "${PROXY_MAX_RPS:-50}"
        printf 'WARP_CONNECT_TIMEOUT=%s\n' "${WARP_CONNECT_TIMEOUT:-30}"
        printf 'AUTO_REFRESH_INTERVAL=%s\n' "${AUTO_REFRESH_INTERVAL:-60}"
        printf 'PROXY_AUTH_ENABLED=%s\n' "${PROXY_AUTH_ENABLED:-false}"
        printf 'PROXY_USER=%s\n' "${PROXY_USER:-}"
        printf 'PROXY_HOST_OMNIROUTE=%s\n' "${PROXY_HOST_OMNIROUTE:-}"
    } > "$WARP_ENV_FILE"
}

gost_auth_block() {
    if [ -n "${PROXY_USER:-}" ] && [ -n "${PROXY_PASS:-}" ]; then
        cat <<EOF

    auth:
      username: ${PROXY_USER}
      password: ${PROXY_PASS}
EOF
    fi
}

gost_admission_blocks() {
    local mode="$1"
    if [ -z "${PROXY_ALLOWED_IPS:-}" ]; then
        return 0
    fi

    if [ "$mode" = "ref" ]; then
        cat <<EOF

  admission: admission-0
EOF
        return 0
    fi

    local matchers=""
    IFS=',' read -ra IPS <<< "$PROXY_ALLOWED_IPS"
    for ip in "${IPS[@]}"; do
        ip=$(echo "$ip" | xargs)
        matchers="${matchers}
  - ${ip}"
    done
    cat <<EOF
admissions:
- name: admission-0
  whitelist: true
  matchers:${matchers}
EOF
}

generate_gost_config_roundrobin() {
    local verify_dir="$1"
    local config_file="${2:-$GOST_CONFIG_FILE}"
    local healthy_file="${3:-$HEALTHY_PORTS_FILE}"
    local ss_pass="${PROXY_PASS:-cloudflare-warp}"
    local ss_method="${SS_METHOD:-chacha20-ietf-poly1305}"
    local climiter_val="${PROXY_MAX_CONN:-10}"
    local rlimiter_val="${PROXY_MAX_RPS:-50}"
    local proxy_auth
    local admission_ref
    local admission_section

    proxy_auth=$(gost_auth_block)
    admission_ref=$(gost_admission_blocks ref)
    admission_section=$(gost_admission_blocks section)

    local nodes=""
    local healthy_ports=""
    for i in $(seq 0 $((WARP_INSTANCES - 1))); do
        if [ -f "${verify_dir}/${i}" ]; then
            local port=$((40000 + i))
            nodes="${nodes}
    - name: warp-${i}
      addr: 127.0.0.1:${port}
      connector:
        type: socks5
      dialer:
        type: tcp"
            healthy_ports="${healthy_ports}${port}\n"
        fi
    done

    printf "%b" "$healthy_ports" > "$healthy_file"

    cat > "$config_file" <<EOF
log:
  level: ${PROXY_LOG_LEVEL:-warn}
services:
- name: socks5-warp
  addr: ":1080"
  handler:
    type: socks5
    chain: warp-chain${proxy_auth}
  listener:
    type: tcp
  climiter: climiter-0
  rlimiter: rlimiter-0${admission_ref}

- name: http-warp
  addr: ":8080"
  handler:
    type: http
    chain: warp-chain${proxy_auth}
  listener:
    type: tcp
  climiter: climiter-0
  rlimiter: rlimiter-0${admission_ref}

- name: ss-warp
  addr: ":8388"
  handler:
    type: ss
    chain: warp-chain
    auth:
      username: ${ss_method}
      password: ${ss_pass}
  listener:
    type: tcp
    climiter: climiter-0
    rlimiter: rlimiter-0${admission_ref}

- name: socks5-direct
  addr: ":1081"
  handler:
    type: socks5${proxy_auth}
  listener:
    type: tcp
    climiter: climiter-0
    rlimiter: rlimiter-0${admission_ref}

- name: http-direct
  addr: ":8081"
  handler:
    type: http${proxy_auth}
  listener:
    type: tcp
    climiter: climiter-0
    rlimiter: rlimiter-0${admission_ref}

- name: ss-direct
  addr: ":8389"
  handler:
    type: ss
    auth:
      username: ${ss_method}
      password: ${ss_pass}
  listener:
    type: tcp
  climiter: climiter-0
  rlimiter: rlimiter-0${admission_ref}

chains:
- name: warp-chain
  hops:
  - name: warp-hop
    selector:
      strategy: round
      maxFails: 3
      failTimeout: 30s
    nodes:${nodes}

climiters:
- name: climiter-0
  limits:
  - '\$ ${climiter_val}'

rlimiters:
- name: rlimiter-0
  limits:
  - '\$ ${rlimiter_val}'
${admission_section}
EOF

    echo "GOST config written to ${config_file}"
}

generate_gost_config_dedicated() {
    local verify_dir="$1"
    local config_file="${2:-$GOST_CONFIG_FILE}"
    local healthy_file="${3:-$HEALTHY_PORTS_FILE}"
    local ss_pass="${PROXY_PASS:-cloudflare-warp}"
    local ss_method="${SS_METHOD:-chacha20-ietf-poly1305}"
    local climiter_val="${PROXY_MAX_CONN:-10}"
    local rlimiter_val="${PROXY_MAX_RPS:-50}"
    local proxy_auth
    local admission_ref
    local admission_section

    proxy_auth=$(gost_auth_block)
    admission_ref=$(gost_admission_blocks ref)
    admission_section=$(gost_admission_blocks section)

    local dedicated_services=""
    local dedicated_chains=""
    local healthy_ports=""

    for i in $(seq 0 $((WARP_INSTANCES - 1))); do
        local warp_port=$((40000 + i))
        local proxy_port=$((PROXY_BASE_PORT + i))
        if [ -n "$verify_dir" ] && [ -f "${verify_dir}/${i}" ]; then
            healthy_ports="${healthy_ports}${warp_port}\n"
            echo "[dedicated] WARP instance $((i + 1)) -> 127.0.0.1:${warp_port} proxy :${proxy_port} (verified)"
        else
            echo "[dedicated] WARP instance $((i + 1)) -> 127.0.0.1:${warp_port} proxy :${proxy_port} (unverified)"
        fi

        dedicated_services="${dedicated_services}
- name: socks5-warp-${i}
  addr: \":${proxy_port}\"
  handler:
    type: socks5
    chain: warp-chain-${i}${proxy_auth}
  listener:
    type: tcp
  climiter: climiter-0
  rlimiter: rlimiter-0${admission_ref}
"

            dedicated_chains="${dedicated_chains}
- name: warp-chain-${i}
  hops:
  - name: warp-hop-${i}
    nodes:
    - name: warp-${i}
      addr: 127.0.0.1:${warp_port}
      connector:
        type: socks5
      dialer:
        type: tcp
"
    done

    printf "%b" "$healthy_ports" > "$healthy_file"

    cat > "$config_file" <<EOF
log:
  level: ${PROXY_LOG_LEVEL:-warn}
services:
${dedicated_services}
- name: socks5-direct
  addr: ":1081"
  handler:
    type: socks5${proxy_auth}
  listener:
    type: tcp
  climiter: climiter-0
  rlimiter: rlimiter-0${admission_ref}

- name: http-direct
  addr: ":8081"
  handler:
    type: http${proxy_auth}
  listener:
    type: tcp
  climiter: climiter-0
  rlimiter: rlimiter-0${admission_ref}

- name: ss-direct
  addr: ":8389"
  handler:
    type: ss
    auth:
      username: ${ss_method}
      password: ${ss_pass}
  listener:
    type: tcp
  climiter: climiter-0
  rlimiter: rlimiter-0${admission_ref}

chains:
${dedicated_chains}

climiters:
- name: climiter-0
  limits:
  - '\$ ${climiter_val}'

rlimiters:
- name: rlimiter-0
  limits:
  - '\$ ${rlimiter_val}'
${admission_section}
EOF

    echo "GOST config written to ${config_file}"
}
