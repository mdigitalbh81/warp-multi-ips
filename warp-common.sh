#!/bin/bash

set -e

WARP_DATA_DIR=${WARP_DATA_DIR:-/var/lib/cloudflare-warp}
ADMIN_CONFIG_FILE=${ADMIN_CONFIG_FILE:-${WARP_DATA_DIR}/admin-config.json}
GOST_CONFIG_FILE=${GOST_CONFIG_FILE:-/tmp/gost-config.yaml}
HEALTHY_PORTS_FILE=${HEALTHY_PORTS_FILE:-/tmp/healthy-warp-ports}
WARP_ENV_FILE=${WARP_ENV_FILE:-/tmp/warp-admin-env}

write_file() {
    local file="$1"
    if { [ -d "$(dirname "$file")" ] && [ -w "$(dirname "$file")" ]; } || { [ -e "$file" ] && [ -w "$file" ]; }; then
        cat > "$file"
    else
        sudo tee "$file" >/dev/null
    fi
}

load_admin_config() {
    if [ "${ADMIN_ENABLED:-false}" = "true" ] && [ -f "$ADMIN_CONFIG_FILE" ]; then
        WARP_INSTANCES=$(jq -r '.instances // env.WARP_INSTANCES // "1"' "$ADMIN_CONFIG_FILE")
        PROXY_MODE=$(jq -r '.proxy_mode // env.PROXY_MODE // "round-robin"' "$ADMIN_CONFIG_FILE")
        PROXY_BASE_PORT=$(jq -r '.proxy_base_port // env.PROXY_BASE_PORT // "2080"' "$ADMIN_CONFIG_FILE")
        PROXY_MAX_RPS=$(jq -r '.proxy_max_rps // env.PROXY_MAX_RPS // "50"' "$ADMIN_CONFIG_FILE")
        WARP_CONNECT_TIMEOUT=$(jq -r '.warp_connect_timeout // env.WARP_CONNECT_TIMEOUT // "30"' "$ADMIN_CONFIG_FILE")
        AUTO_REFRESH_INTERVAL=$(jq -r '.auto_refresh_interval // env.AUTO_REFRESH_INTERVAL // "60"' "$ADMIN_CONFIG_FILE")
        PROXY_AUTH_ENABLED=$(jq -r '.proxy_auth_enabled // false' "$ADMIN_CONFIG_FILE")
        CFG_PROXY_USER=$(jq -r '.proxy_user // ""' "$ADMIN_CONFIG_FILE")
        CFG_PROXY_PASS=$(jq -r '.proxy_password // ""' "$ADMIN_CONFIG_FILE")
        if [ "$PROXY_AUTH_ENABLED" = "true" ]; then
            PROXY_USER="$CFG_PROXY_USER"
            PROXY_PASS="$CFG_PROXY_PASS"
        else
            PROXY_USER=""
            PROXY_PASS=""
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
            '{
              instances: $instances,
              proxy_mode: $proxy_mode,
              proxy_base_port: $proxy_base_port,
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

    if ! [[ "$WARP_INSTANCES" =~ ^[0-9]+$ ]] || [ "$WARP_INSTANCES" -lt 1 ]; then
        echo "Error: WARP_INSTANCES must be a positive integer"
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
        if [ -f "${verify_dir}/${i}" ]; then
            local warp_port=$((40000 + i))
            local proxy_port=$((PROXY_BASE_PORT + i))
            healthy_ports="${healthy_ports}${warp_port}\n"

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
            echo "[dedicated] WARP instance $((i+1)) -> 127.0.0.1:${warp_port} -> proxy :${proxy_port}"
        else
            echo "[dedicated] WARP instance $((i+1)) -> SKIPPED (failed verification)"
        fi
    done

    printf "%b" "$healthy_ports" > "$healthy_file"

    cat > "$config_file" <<EOF
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
