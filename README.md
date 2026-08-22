# WARP Multi IPs

[![Build](https://img.shields.io/github/actions/workflow/status/mdigitalbh81/warp-multi-ips/build-test-push.yml?logo=github&label=Build)](https://github.com/mdigitalbh81/warp-multi-ips/actions)
[![GitHub Stars](https://img.shields.io/github/stars/mdigitalbh81/warp-multi-ips?logo=github&label=Stars)](https://github.com/mdigitalbh81/warp-multi-ips)
[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-blue.svg)](LICENSE)

## Attribution

This project is derived from [https://github.com/ErcinDedeoglu/cloudflare-warp](https://github.com/ErcinDedeoglu/cloudflare-warp).

Original project by Ercin Dedeoglu and contributors. This fork adds dedicated per-instance proxy ports while preserving the original CC BY-NC 4.0 license. Non-commercial use only.

Upstream project: [ErcinDedeoglu/cloudflare-warp](https://github.com/ErcinDedeoglu/cloudflare-warp). The upstream Docker image `dublok/cloudflare-warp:latest` belongs to the original project and may not include the changes from this fork.

Run [Cloudflare WARP](https://1.1.1.1/) in Docker. Provides SOCKS5 and HTTP proxies that route traffic through Cloudflare's network. Supports multiple WARP instances in a single container for IP rotation.

## Quick Start

```yaml
services:
  warp:
    build:
      context: .
    container_name: warp
    restart: always
    ports:
      - "1080:1080"  # SOCKS5 proxy
      # - "8080:8080"  # HTTP proxy
    volumes:
      - warp-data:/var/lib/cloudflare-warp

volumes:
  warp-data:
```

```bash
docker compose up -d

# Test SOCKS5 proxy
curl --socks5-hostname 127.0.0.1:1080 https://cloudflare.com/cdn-cgi/trace

# Test HTTP proxy (if port 8080 exposed)
curl -x http://127.0.0.1:8080 https://cloudflare.com/cdn-cgi/trace
```

If working, you'll see `warp=on` in the output.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `WARP_INSTANCES` | Number of WARP instances. Each gets a unique Cloudflare IP. Traffic is round-robined across all instances. No extra capabilities required | `1` |
| `PROXY_MODE` | Proxy mode: `round-robin` (shared ports, IP rotation) or `dedicated` (one SOCKS5 port per instance) | `round-robin` |
| `PROXY_BASE_PORT` | Base port for dedicated mode. Instance N listens on `PROXY_BASE_PORT + N`. Ignored in round-robin mode | `2080` |
| `WARP_LICENSE_KEY` | WARP+ license key. Comma-separated for multiple keys — tries each in order, skips any that fail | - |
| `WARP_ORG` | Zero Trust team name. Enables automatic enrollment via service token (see [Zero Trust](#zero-trust-free-warp-routing) section). Mutually exclusive with `WARP_LICENSE_KEY` | - |
| `WARP_AUTH_CLIENT_ID` | Service token Client ID (required when `WARP_ORG` is set) | - |
| `WARP_AUTH_CLIENT_SECRET` | Service token Client Secret (required when `WARP_ORG` is set) | - |
| `WARP_CONNECT_TIMEOUT` | Max seconds to wait for WARP daemon | `30` |
| `PROXY_USER` | Proxy authentication username | - |
| `PROXY_PASS` | Proxy authentication password | - |
| `PROXY_ALLOWED_IPS` | IP whitelist (comma-separated CIDRs) | - |
| `PROXY_MAX_CONN` | Max concurrent connections per IP | `10` |
| `PROXY_MAX_RPS` | Max requests per second per IP | `50` |
| `SS_METHOD` | Shadowsocks encryption method | `chacha20-ietf-poly1305` |

## With Authentication

```yaml
services:
  warp:
    build:
      context: .
    ports:
      - "1080:1080"  # SOCKS5 proxy
      - "8080:8080"  # HTTP proxy
    environment:
      - PROXY_USER=myuser
      - PROXY_PASS=mypassword
    volumes:
      - warp-data:/var/lib/cloudflare-warp

volumes:
  warp-data:
```

```bash
# SOCKS5 with auth
curl --socks5-hostname myuser:mypassword@127.0.0.1:1080 https://cloudflare.com/cdn-cgi/trace

# HTTP with auth
curl -x http://myuser:mypassword@127.0.0.1:8080 https://cloudflare.com/cdn-cgi/trace
```

## Direct Proxy (Bypass WARP)

Direct proxies are always available that exit through Docker's network without routing through WARP. Useful when you need your real IP for certain services.

| Port | Protocol | Route |
|------|----------|-------|
| 1080 | SOCKS5 | Through WARP (Cloudflare IP) |
| 1081 | SOCKS5 | Direct (real IP) |
| 8080 | HTTP | Through WARP (Cloudflare IP) |
| 8081 | HTTP | Direct (real IP) |

```yaml
services:
  warp:
    build:
      context: .
    ports:
      - "1080:1080"  # SOCKS5 WARP proxy
      - "1081:1081"  # SOCKS5 Direct proxy
      - "8080:8080"  # HTTP WARP proxy
      - "8081:8081"  # HTTP Direct proxy
    environment:
      - PROXY_USER=myuser
      - PROXY_PASS=mypassword
    volumes:
      - warp-data:/var/lib/cloudflare-warp

volumes:
  warp-data:
```

```bash
# SOCKS5 through WARP (Cloudflare IP)
curl --socks5-hostname myuser:mypassword@127.0.0.1:1080 https://ifconfig.me

# SOCKS5 direct exit (your real IP)
curl --socks5-hostname myuser:mypassword@127.0.0.1:1081 https://ifconfig.me

# HTTP through WARP (Cloudflare IP)
curl -x http://myuser:mypassword@127.0.0.1:8080 https://ifconfig.me

# HTTP direct exit (your real IP)
curl -x http://myuser:mypassword@127.0.0.1:8081 https://ifconfig.me
```

## Multi-Instance (IP Rotation / Round-Robin)

Set `WARP_INSTANCES=N` to run multiple WARP daemons in a single container, each with a unique Cloudflare IP. By default (`PROXY_MODE=round-robin`), traffic is round-robined across all instances on the same ports — no extra capabilities required.

```yaml
environment:
  - WARP_INSTANCES=10    # each request exits through a different IP
```

Each instance uses ~50-100 MB RAM and starts 2 seconds apart. If an instance fails, GOST skips it after 3 failures and retries after 30s.

## Dedicated Proxy Mode (1 Port per IP)

Set `PROXY_MODE=dedicated` to expose each WARP instance on its own SOCKS5 port. Every port is deterministically bound to one WARP instance — no round-robin, no IP rotation. Useful when you need to register each proxy independently in another system.

```yaml
services:
  warp:
    build:
      context: .
    ports:
      - "2080-2082:2080-2082"   # dedicated SOCKS5 ports
      # - "1081:1081"           # direct proxy (always available)
    environment:
      - WARP_INSTANCES=3
      - PROXY_MODE=dedicated
      - PROXY_BASE_PORT=2080
    volumes:
      - warp-data:/var/lib/cloudflare-warp

volumes:
  warp-data:
```

This produces:

```text
port 2080 -> WARP instance 1 -> IP A
port 2081 -> WARP instance 2 -> IP B
port 2082 -> WARP instance 3 -> IP C
```

With 10 instances:

```yaml
environment:
  - WARP_INSTANCES=10
  - PROXY_MODE=dedicated
  - PROXY_BASE_PORT=2080
```

Generates ports 2080-2089, each bound to a single WARP instance.

### Verifying Dedicated Mode

Test each port individually:

```bash
curl --proxy socks5h://127.0.0.1:2080 https://www.cloudflare.com/cdn-cgi/trace
curl --proxy socks5h://127.0.0.1:2081 https://www.cloudflare.com/cdn-cgi/trace
curl --proxy socks5h://127.0.0.1:2082 https://www.cloudflare.com/cdn-cgi/trace
```

Or use the included test script to check all ports at once:

```bash
./scripts/test-dedicated.sh 3 2080
```

Each port should consistently return the same IP across repeated requests. Two instances may occasionally receive the same Cloudflare exit IP — this is normal Cloudflare behavior, not an implementation bug. What matters is that each port is locked to its own WARP instance.

### Notes on Dedicated Mode

- Direct proxies (SOCKS5 `:1081`, HTTP `:8081`, SS `:8389`) remain available and bypass WARP regardless of mode.
- HTTP and Shadowsocks WARP proxies (`:8080`, `:8388`) are **not** created in dedicated mode — only SOCKS5 per-instance listeners are generated. Use the SOCKS5 port for each instance.
- Proxy authentication (`PROXY_USER`/`PROXY_PASS`) applies to all dedicated ports.
- Port conflicts with fixed service ports (1081, 8080, 8081, 8388, 8389) are detected at startup.

## Zero Trust (Free WARP+ Routing)

Enroll devices into Cloudflare Zero Trust using service tokens for free WARP+ equivalent routing — no browser needed. See the **[Zero Trust setup guide](docs/zero-trust.md)** for configuration and usage.

## Mobile VPN (Shadowsocks)

Connect your mobile devices using Shadowsocks apps - works as a system-wide VPN without requiring special Docker privileges. **Shadowsocks is always enabled** on ports 8388/8389.

### Supported Apps

| Platform | App | Price |
|----------|-----|-------|
| Android | [Shadowsocks](https://play.google.com/store/apps/details?id=com.github.shadowsocks) | Free |
| Android | [v2rayNG](https://play.google.com/store/apps/details?id=com.v2ray.ang) | Free |
| iOS | [Shadowrocket](https://apps.apple.com/app/shadowrocket/id932747118) | ~$3 |
| iOS | [Potatso Lite](https://apps.apple.com/app/potatso-lite/id1239860606) | Free |

### Setup

```yaml
services:
  warp:
    build:
      context: .
    ports:
      - "8388:8388"  # Shadowsocks WARP (Cloudflare IP)
      - "8389:8389"  # Shadowsocks Direct (real IP)
    environment:
      - PROXY_PASS=your-secure-password  # Optional: sets password for all protocols
    volumes:
      - warp-data:/var/lib/cloudflare-warp

volumes:
  warp-data:
```

### Mobile App Configuration

| Setting | Value |
|---------|-------|
| Server | Your server IP or domain |
| Port | `8388` (WARP) or `8389` (Direct) |
| Password | Your `PROXY_PASS` or `cloudflare-warp` (default) |
| Method | `chacha20-ietf-poly1305` (default) |

### Available Encryption Methods

**Recommended (AEAD):**
- `chacha20-ietf-poly1305` (default, recommended for mobile)
- `aes-256-gcm`
- `aes-128-gcm`

**Shadowsocks 2022 (newest, requires base64 key as password):**
- `2022-blake3-aes-128-gcm`
- `2022-blake3-aes-256-gcm`
- `2022-blake3-chacha20-poly1305`

**Other:**
- `xchacha20-ietf-poly1305`
- `chacha20-poly1305`

### Port Reference

| Port | Protocol | Route |
|------|----------|-------|
| 8388 | Shadowsocks | Through WARP (Cloudflare IP) |
| 8389 | Shadowsocks | Direct (real IP) |

## License

CC-BY-NC-4.0 - Non-commercial use only with attribution.
