# Cloudflare WARP Multi-IP Lightweight Proxy

[![Build](https://img.shields.io/github/actions/workflow/status/mdigitalbh81/warp-multi-ips/build-test-push.yml?logo=github&label=Build)](https://github.com/mdigitalbh81/warp-multi-ips/actions)
[![GitHub Stars](https://img.shields.io/github/stars/mdigitalbh81/warp-multi-ips?logo=github&label=Stars)](https://github.com/mdigitalbh81/warp-multi-ips)
[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-blue.svg)](LICENSE)

## Attribution
This project is derived from [https://github.com/ErcinDedeoglu/cloudflare-warp](https://github.com/ErcinDedeoglu/cloudflare-warp) by Ercin Dedeoglu and contributors, licensed under CC BY-NC 4.0. Non-commercial use only.

This fork implements an ultra-lightweight, multi-instance Cloudflare WARP architecture powered by `wireproxy` userspace engine, `wgcf`, and `GOST`. It delivers guaranteed unique IPv6 egress routing, high concurrency, and low memory consumption for OmniRoute, web scrapers, and automation workloads.

> **Historical Note:**
> Older versions used the official Cloudflare WARP daemon (`warp-svc`) per instance. This was replaced by a lightweight `wireproxy` architecture due to resource usage (~5.8MB PSS vs ~104MB PSS per instance).

## Quick Start

```yaml
services:
  warp:
    build:
      context: .
    container_name: warp
    restart: always
    ports:
      - "2080-2089:2080-2089"  # Dedicated SOCKS5 proxy ports (instances 1-10)
      - "9090:9090"            # Web Admin Dashboard & API
    environment:
      - WARP_INSTANCES=10
      - PROXY_MODE=dedicated
      - PROXY_BASE_PORT=2080
      - ADMIN_ENABLED=true
      - ADMIN_PASSWORD=your-secure-admin-password
      - PROXY_HOST_OMNIROUTE=omniroute_warp-proxy
    volumes:
      - warp-data:/var/lib/cloudflare-warp

volumes:
  warp-data:
```

```bash
docker compose up -d

# Test SOCKS5 proxy on instance 1 (port 2080)
curl --socks5-hostname 127.0.0.1:2080 https://cloudflare.com/cdn-cgi/trace

# Verify unique IPv6 egress
curl --socks5-hostname 127.0.0.1:2080 https://api6.ipify.org
```

When working, you will see `warp=on` and distinct public IPv6 addresses on each dedicated port.

## Architecture

```text
OmniRoute / Downstream Clients
            │
            ▼
    GOST Proxy Router (Ports 2080..2089 or 1080)
            │
            ▼
    wireproxy instances (127.0.0.1:40000..40009)
            │
            ▼ (WireGuard WireProxy Userspace Tunnel via wgcf)
    Cloudflare WARP Edge
            │
            ▼
    Unique IPv6 Egress per Instance (2a09:bac5:...)
```

### Core Benefits

1. **Ultra-Low Memory Footprint (PSS):** Consumes ~5.8 MB PSS per instance (~58 MB PSS for 10 instances), compared to ~104 MB PSS per instance (>1 GB for 10 instances) under the legacy `warp-svc` daemon.
2. **Unique IPv6 Egress Guarantee:** Cloudflare assigns distinct IPv6 addresses to separate device registrations. Each instance egress is verified against `https://api6.ipify.org` and monitored continuously by the watchdog.
3. **Zero-Touch Deployment:** Deploy directly with Dokploy, EasyPanel, or Docker Compose without configuring engine flags or manual registration.
4. **Deterministic Dedicated Ports:** In `dedicated` mode, each port (`PROXY_BASE_PORT + N`) maps to its own wireproxy tunnel instance.
5. **OmniRoute Integration:** Built-in dashboard and `/api/export/omniroute` endpoint to copy or export proxy lists instantly.
6. **Persistent State & Identities:** Registrations, WireGuard profiles, admin credentials, and notes are stored in `/var/lib/cloudflare-warp` and preserved across restarts.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `WARP_INSTANCES` | Number of WARP instances to run | `10` |
| `PROXY_MODE` | Proxy mode: `dedicated` (one port per instance) or `round-robin` (shared port 1080) | `dedicated` |
| `PROXY_BASE_PORT` | Base port for dedicated mode. Instance N listens on `PROXY_BASE_PORT + N`. Ignored in round-robin mode | `2080` |
| `PROXY_HOST_OMNIROUTE` | Host or IP that OmniRoute uses to reach the SOCKS5 proxy ports (e.g. `10.0.0.254`, `proxy.example.com`). Displayed in the admin dashboard as OmniRoute Proxy. Does not change proxy binding | - |
| `PROXY_HOST` | **Deprecated.** Fallback for `PROXY_HOST_OMNIROUTE` when the new variable is not set | - |
| `LIGHTWEIGHT_EGRESS_FAMILY` | Egress address family: `ipv6` (recommended), `ipv4`, or `auto` | `ipv6` |
| `LIGHTWEIGHT_REQUIRE_UNIQUE_EGRESS` | Require unique IPv6 egress per wireproxy instance; flags collisions as degraded | `true` |
| `LIGHTWEIGHT_REGISTRATION_DELAY` | Stagger delay (seconds) between initial `wgcf` device registrations | `2` |
| `LIGHTWEIGHT_EGRESS_CHECK_INTERVAL` | Periodic interval (seconds) for watchdog IPv6 egress validation | `60` |
| `WARP_CONNECT_TIMEOUT` | Max seconds to wait for wireproxy instance initialization | `30` |
| `WARP_LICENSE_KEY` | Optional WARP+ license key(s). Comma-separated for multiple keys | - |
| `PROXY_USER` | Proxy authentication username | - |
| `PROXY_PASS` | Proxy authentication password | - |
| `PROXY_ALLOWED_IPS` | IP whitelist (comma-separated CIDRs) | - |
| `PROXY_MAX_CONN` | Max concurrent connections per IP | `200` |
| `PROXY_MAX_RPS` | Max requests per second per IP | `50` |
| `SS_METHOD` | Shadowsocks encryption method | `chacha20-ietf-poly1305` |
| `ADMIN_ENABLED` | Enable the protected web admin panel. When enabled for the first time, it imports env values into persistent admin config | `false` |
| `ADMIN_PORT` | Admin panel port inside the container. Publish it explicitly in Compose only when needed | `9090` |
| `ADMIN_USER` | Initial admin panel username. Used only to create persistent credentials on first startup | `admin` |
| `ADMIN_PASSWORD` | Admin panel password. Required when `ADMIN_ENABLED=true` | - |
| `ADMIN_MAX_INSTANCES` | Safety limit for instance count changes from the panel | `45` |
| `AUTO_REFRESH_INTERVAL` | Seconds between cached Current Egress IP refreshes | `60` |
| `WARP_WATCHDOG_ENABLED` | Enable background health watchdog | `true` |
| `WARP_WATCHDOG_INTERVAL` | Interval between health checks (seconds) | `30` |
| `WARP_WATCHDOG_FAILURE_THRESHOLD` | Consecutive failures before marking degraded / recovering | `3` |
| `WARP_WATCHDOG_RECOVERY_TIMEOUT` | Max seconds to wait for recovery | `30` |
| `WARP_WATCHDOG_RESTART_COOLDOWN` | Cooldown period between restarts (seconds) | `120` |

## Dedicated Proxy Mode (1 Port per Instance)

Set `PROXY_MODE=dedicated` to expose each WARP instance on its own dedicated SOCKS5 port. Every port is deterministically bound to one WARP instance.

```yaml
services:
  warp:
    build:
      context: .
    ports:
      - "2080-2089:2080-2089"   # dedicated SOCKS5 ports (10 instances)
      - "9090:9090"             # admin panel
    environment:
      - WARP_INSTANCES=10
      - PROXY_MODE=dedicated
      - PROXY_BASE_PORT=2080
    volumes:
      - warp-data:/var/lib/cloudflare-warp

volumes:
  warp-data:
```

Port mapping:
`port 2080 -> WARP instance 1 -> Unique IPv6 Egress A`
`port 2081 -> WARP instance 2 -> Unique IPv6 Egress B`
`...`
`port 2089 -> WARP instance 10 -> Unique IPv6 Egress J`

### Verifying Dedicated Proxies

```bash
./scripts/test-dedicated.sh 10 2080
```

## Web Admin Panel

The admin dashboard is available on port 9090 (`ADMIN_ENABLED=true`). It displays:

- **Instances Overview Table:** 11 detailed columns:
  1. Instance Index
  2. OmniRoute Proxy
  3. Current Egress IP
  4. Previous IPv6
  5. Unique status badge
  6. Country
  7. Cloudflare Colo (e.g. GRU)
  8. Editable Notes
  9. WARP Status
  10. Health Status
  11. Actions (Restart / Reprovision)
- **OmniRoute Export Panel:** Ready-to-copy or downloadable proxy list for OmniRoute configuration.
- **Runtime Management:** Dynamic scaling, egress family selector (`ipv6`, `ipv4`, `auto`), and live health monitoring.

### Admin API Endpoints

```text
GET  /api/status
GET  /api/instances
GET  /api/config
POST /api/config
POST /api/admin/credentials
POST /api/refresh
POST /api/instances/refresh
POST /api/instances/{id}/restart
POST /api/instances/{id}/reprovision
PATCH /api/instances/{id}/note
GET  /api/export/omniroute
GET  /health
```

## Direct Proxy (Bypass WARP)

Direct proxies exit directly through Docker network without routing through WARP:
| Port | Protocol | Route |
|------|----------|-------|
| 1081 | SOCKS5 | Direct (real IP) |
| 8081 | HTTP | Direct (real IP) |
| 8389 | Shadowsocks | Direct (real IP) |

## License

CC-BY-NC-4.0 - Non-commercial use only with attribution.
