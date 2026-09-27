ARG BASE_IMAGE=ubuntu:24.04

FROM ${BASE_IMAGE}

ARG COMMIT_SHA
ARG TARGETPLATFORM

LABEL org.opencontainers.image.title="Cloudflare WARP"
LABEL org.opencontainers.image.description="Docker container for Cloudflare WARP client with GOST proxy support"
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
        *) echo "Unsupported TARGETPLATFORM: ${TARGETPLATFORM}" && exit 1 ;; \
      esac; \
    else \
      case "$(dpkg --print-architecture)" in \
        "amd64") ARCH="amd64" ;; \
        "arm64") ARCH="arm64" ;; \
        *) echo "Unsupported local architecture: $(dpkg --print-architecture)" && exit 1 ;; \
      esac; \
    fi && \
    apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg lsb-release sudo jq dbus python3 && \
    curl https://pkg.cloudflareclient.com/pubkey.gpg | gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/cloudflare-client.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends cloudflare-warp && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    GOST_VERSION=$(curl -s https://api.github.com/repos/go-gost/gost/releases/latest | jq -r '.tag_name' | sed 's/^v//') && \
    echo "Installing GOST version: ${GOST_VERSION}" && \
    FILE_NAME="gost_${GOST_VERSION}_linux_${ARCH}.tar.gz" && \
    curl -fLO "https://github.com/go-gost/gost/releases/download/v${GOST_VERSION}/${FILE_NAME}" && \
    tar -xzf ${FILE_NAME} -C /usr/bin/ gost && \
    rm -f ${FILE_NAME}     && chmod +x /usr/bin/gost     && WGCF_VERSION="2.2.32"     && if [ "" = "amd64" ]; then         WGCF_SHA256="2ff97f2201972ce582a424455d50a3719a380eef0cd1f3144f7779348e122a2c"         && WIREPROXY_SHA256="e88c1d090740373fc606c1bafd81d9a5eadc642cce5667616e20e9d7a444f51c";     elif [ "" = "arm64" ]; then         WGCF_SHA256="21fe21d9f61db9b381d71200f6f59c7949e0bb455446edcb33dda6ad6a8fcf8f"         && WIREPROXY_SHA256="370e00bd2167960d1ecd1c3c1439715bbaa94a0a110a2040468670c9af6021b6";     fi     && curl -fsSL -o /usr/bin/wgcf "https://github.com/ViRb3/wgcf/releases/download/v${WGCF_VERSION}/wgcf_${WGCF_VERSION}_linux_${ARCH}"     && echo "${WGCF_SHA256}  /usr/bin/wgcf" | sha256sum -c -     && chmod +x /usr/bin/wgcf     && WIREPROXY_VERSION="1.1.3"     && curl -fsSL -o /tmp/wireproxy.tar.gz "https://github.com/pufferffish/wireproxy/releases/download/v${WIREPROXY_VERSION}/wireproxy_linux_${ARCH}.tar.gz"     && echo "${WIREPROXY_SHA256}  /tmp/wireproxy.tar.gz" | sha256sum -c -     && tar -xzf /tmp/wireproxy.tar.gz -C /usr/bin/ wireproxy     && rm -f /tmp/wireproxy.tar.gz     && chmod +x /usr/bin/wireproxy     && chmod +x /entrypoint.sh     && chmod +x /start-warp-instance.sh     && chmod +x /start-wireproxy-instance.sh     && chmod +x /warp-common.sh     && chmod +x /watchdog.sh     && chmod +x /admin/server.py     && chmod +x /healthcheck/index.sh     && useradd -s /bin/bash warp     && echo "warp ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/warp

USER warp

RUN mkdir -p /home/warp/.local/share/warp && \
    echo -n 'yes' > /home/warp/.local/share/warp/accepted-tos.txt

ENV WARP_ENGINE=official
ENV LIGHTWEIGHT_EGRESS_FAMILY=ipv6
ENV LIGHTWEIGHT_REQUIRE_UNIQUE_EGRESS=true
ENV LIGHTWEIGHT_REGISTRATION_DELAY=2
ENV LIGHTWEIGHT_EGRESS_CHECK_INTERVAL=60

ENV WARP_INSTANCES=1
ENV WARP_CONNECT_TIMEOUT=30
ENV PROXY_MODE=round-robin
ENV PROXY_BASE_PORT=2080
ENV PROXY_USER=
ENV PROXY_PASS=
ENV PROXY_MAX_CONN=10
ENV PROXY_MAX_RPS=50
ENV PROXY_ALLOWED_IPS=
ENV SS_METHOD=chacha20-ietf-poly1305
ENV ADMIN_ENABLED=false
ENV ADMIN_PORT=9090
ENV ADMIN_USER=admin
ENV ADMIN_PASSWORD=
ENV ADMIN_MAX_INSTANCES=45
ENV MAX_WARP_INSTANCES=45
ENV AUTO_REFRESH_INTERVAL=60
ENV WARP_WATCHDOG_ENABLED=true
ENV WARP_WATCHDOG_INTERVAL=30
ENV WARP_WATCHDOG_FAILURE_THRESHOLD=3
ENV WARP_WATCHDOG_RECOVERY_TIMEOUT=30
ENV WARP_WATCHDOG_RESTART_COOLDOWN=120

HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=3 \
  CMD /healthcheck/index.sh

ENTRYPOINT ["/entrypoint.sh"]
