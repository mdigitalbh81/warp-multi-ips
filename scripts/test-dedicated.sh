#!/bin/bash

# Test script for dedicated proxy mode.
# Verifies that each port is bound to a distinct WARP instance
# by checking the exit IP multiple times per port.
#
# Usage:
#   ./scripts/test-dedicated.sh [instances] [base_port] [host] [repeats]
#
# Examples:
#   ./scripts/test-dedicated.sh 3 1080           # 3 instances, base 1080, localhost
#   ./scripts/test-dedicated.sh 10 1080 myhost 5 # 10 instances, 5 checks each

set -e

INSTANCES=${1:-3}
BASE_PORT=${2:-2080}
HOST=${3:-127.0.0.1}
REPEATS=${4:-3}

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=== Dedicated Proxy Mode Test ===${NC}"
echo "  Instances : ${INSTANCES}"
echo "  Base port : ${BASE_PORT}"
echo "  Host      : ${HOST}"
echo "  Repeats   : ${REPEATS}"
echo ""

PASS=0
FAIL=0

for i in $(seq 0 $((INSTANCES - 1))); do
    PORT=$((BASE_PORT + i))
    INSTANCE=$((i + 1))
    echo -e "${YELLOW}--- Proxy ${INSTANCE} (port ${PORT}) ---${NC}"

    PREV_IP=""
    CONSISTENT=true

    for r in $(seq 1 "$REPEATS"); do
        IP=$(curl --proxy "socks5h://${HOST}:${PORT}" -s --max-time 15 https://ifconfig.me 2>/dev/null || echo "FAILED")

        if [ "$IP" = "FAILED" ] || [ -z "$IP" ]; then
            echo -e "  Request ${r}: ${RED}FAILED${NC}"
            CONSISTENT=false
            continue
        fi

        echo -e "  Request ${r}: ${GREEN}${IP}${NC}"

        if [ -n "$PREV_IP" ] && [ "$IP" != "$PREV_IP" ]; then
            echo -e "  ${RED}WARNING: IP changed from ${PREV_IP} to ${IP} (possible round-robin leak)${NC}"
            CONSISTENT=false
        fi
        PREV_IP="$IP"
    done

    if [ "$CONSISTENT" = true ] && [ -n "$PREV_IP" ]; then
        echo -e "  Result: ${GREEN}STABLE${NC} (all requests -> ${PREV_IP})"
        PASS=$((PASS + 1))
    else
        echo -e "  Result: ${RED}UNSTABLE or FAILED${NC}"
        FAIL=$((FAIL + 1))
    fi
    echo ""
done

echo -e "${BLUE}=== Summary ===${NC}"
echo -e "  Passed: ${GREEN}${PASS}${NC}"
echo -e "  Failed: ${RED}${FAIL}${NC}"
echo ""

if [ "$FAIL" -eq 0 ]; then
    echo -e "${GREEN}All ${INSTANCES} dedicated proxies are stable.${NC}"
    exit 0
else
    echo -e "${RED}${FAIL} proxy(ies) showed instability.${NC}"
    exit 1
fi
