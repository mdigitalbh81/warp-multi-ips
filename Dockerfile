ARG BASE_IMAGE=ubuntu:24.04

FROM ${BASE_IMAGE}

ARG COMMIT_SHA
ARG TARGETPLATFORM

LABEL org.opencontainers.image.title="Cloudflare WARP"
LABEL org.opencontainers.image.description="Lightweight Cloudflare WARP multi-IP proxy (wireproxy + GOST)"
LABEL org.opencontainers.image.authors="Ercin Dedeoglu <e.dedeoglu@gmail.com>"
LABEL org.opencontainers.image.url="https://github.com/ErcinDedeoglu/cloudflare-warp"
LABEL org.opencontainers.image.source="https://github.com/ErcinDedeoglu/cloudflare-warp"
LABEL org.opencontainers.image.documentation="https://github.com/ErcinDedeoglu/cloudflare-warp#readme"
LABEL org.opencontainers.image.vendor="Ercin Dedeoglu"
LABEL org.opencontainers.image.licenses="CC-BY-NC-4.0"
LABEL org.opencontainers.image.revision=${COMMIT_SHA}
LABEL COMMIT_SHA=${COMMIT_SHA}

COPY entrypoint.sh /entrypoint.sh
COPY start-warp-instance.sh /start-warp-instance.sh
COPY start-wireproxy-instance.sh /start-wireproxy-instance.sh
COPY warp-common.sh /warp-common.sh
COPY watchdog.sh /watchdog.sh
COPY admin /admin
COPY ./healthcheck /healthcheck

RUN if [ -n "${TARGETPLATFORM}" ]; then \
    case ${TARGETPLATFORM} in \
    "linux/amd64") ARCH="amd64" ;; \
    "linux/arm64") ARCH="arm64" ;; \
    *) echo "Unsupported TARGETPLATFORM: ${TARGETPLATFORM}"; exit 1 ;; \
    esac; \
    else \
    case "$(dpkg --print-architecture)" in \
    "amd64") ARCH="amd64" ;; \
    "arm64") ARCH="arm64" ;; \
    *) echo "Unsupported local architecture: $(dpkg --print-architecture)"; exit 1 ;; \
    esac; \
    fi; \
    apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends ca-certificates curl sudo python3 jq && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    GOST_VERSION="3.3.0" && \
    echo "Installing GOST version: ${GOST_VERSION}" && \
    if [ "$ARCH" = "amd64" ]; then \
        GOST_SHA256="676fb7f78d267b6ae73df719c0c7f2b565dde7147da935cfafbc1e1da558b6d5"; \
        WGCF_SHA256="01614e38c0eb5f3405232e71cfaf02d64d4809e4988ad8f5a8071af16d193405"; \
        WIREPROXY_SHA256="e88c1d090740373fc606c1bafd81d9a5eadc642cce5667616e20e9d7a444f51c"; \
    elif [ "$ARCH" = "arm64" ]; then \
        GOST_SHA256="d03699e3f385d4ff5dad68046712adfcc7515325a064d2ab046e0bece30f8f8f"; \
        WGCF_SHA256="dcadadc42bcc410a4032a6d1c0490ea510e199f0aaaee397dc1aa0fbd27038e8"; \
        WIREPROXY_SHA256="370e00bd2167960d1ecd1c3c1439715bbaa94a0a110a2040468670c9af6021b6"; \
    else \
        echo "Unsupported ARCH: ${ARCH}" >&2; exit 1; \
    fi && \
    FILE_NAME="gost_${GOST_VERSION}_linux_${ARCH}.tar.gz" && \
    curl -fsSL -o "/tmp/${FILE_NAME}" "https://github.com/go-gost/gost/releases/download/v${GOST_VERSION}/${FILE_NAME}" && \
    echo "${GOST_SHA256}  /tmp/${FILE_NAME}" | sha256sum -c - && \
    tar -xzf "/tmp/${FILE_NAME}" -C /usr/bin/ gost && \
    rm -f "/tmp/${FILE_NAME}" && \
    chmod +x /usr/bin/gost && \
    WGCF_VERSION="2.3.0" && \
    curl -fsSL -o /usr/bin/wgcf "https://github.com/ViRb3/wgcf/releases/download/v${WGCF_VERSION}/wgcf_${WGCF_VERSION}_linux_${ARCH}" && \
    echo "${WGCF_SHA256}  /usr/bin/wgcf" | sha256sum -c && \
    chmod +x /usr/bin/wgcf && \
    WIREPROXY_VERSION="1.1.3" && \
    curl -fsSL -o /tmp/wireproxy.tar.gz "https://github.com/windtf/wireproxy/releases/download/v${WIREPROXY_VERSION}/wireproxy_linux_${ARCH}.tar.gz" && \
    echo "${WIREPROXY_SHA256}  /tmp/wireproxy.tar.gz" | sha256sum -c && \
    tar -xzf /tmp/wireproxy.tar.gz -C /usr/bin/ wireproxy && \
    rm -f /tmp/wireproxy.tar.gz && \
    chmod +x /usr/bin/wireproxy && \
    chmod +x /entrypoint.sh && \
    chmod +x /start-warp-instance.sh && \
    chmod +x /start-wireproxy-instance.sh && \
    chmod +x /warp-common.sh && \
    chmod +x /watchdog.sh && \
    chmod +x /admin/server.py && \
    chmod +x /healthcheck/index.sh && \
    useradd -s /bin/bash -m warp && \
    echo "warp ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/warp

USER warp

ENV LIGHTWEIGHT_EGRESS_FAMILY=ipv6
ENV LIGHTWEIGHT_REQUIRE_UNIQUE_EGRESS=true
ENV LIGHTWEIGHT_REGISTRATION_DELAY=2
ENV LIGHTWEIGHT_EGRESS_CHECK_INTERVAL=60

ENV SS_METHOD=chacha20-ietf-poly1305
ENV ADMIN_ENABLED=false
ENV ADMIN_PORT=9090
ENV ADMIN_USER=admin
ENV ADMIN_PASSWORD=
ENV ADMIN_MAX_INSTANCES=45
ENV MAX_WARP_INSTANCES=45
ENV WARP_WATCHDOG_ENABLED=true
ENV WARP_WATCHDOG_INTERVAL=30
ENV WARP_WATCHDOG_FAILURE_THRESHOLD=3
ENV WARP_WATCHDOG_RECOVERY_TIMEOUT=30
ENV WARP_WATCHDOG_RESTART_COOLDOWN=120

HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=3 \
    CMD /healthcheck/index.sh

ENTRYPOINT ["/entrypoint.sh"]
