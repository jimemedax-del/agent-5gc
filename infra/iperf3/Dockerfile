# Preserve the current gNB/UE software; add a durable UE benchmark client.
# The verified base-image digest is supplied during the build.
ARG BASE_IMAGE=free5gc/ueransim@sha256:4a6745b0c9f0c60173833f8bef89816324e84636e220917bdc682555a299e8ba
FROM ${BASE_IMAGE}
USER root
# Supply this public CA bundle from the build host (no private keys).
COPY ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
# Debian 11 packages have moved to the archive. Keep package-signature and
# TLS verification enabled; only the archive's metadata expiry is ignored.
RUN sed -i 's|http://deb.debian.org|https://archive.debian.org|g; s|http://security.debian.org|https://archive.debian.org|g' /etc/apt/sources.list \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get -o Acquire::Retries=2 -o Acquire::Check-Valid-Until=false update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates iperf3 \
    && rm -rf /var/lib/apt/lists/*
