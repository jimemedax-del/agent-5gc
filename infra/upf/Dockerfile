# Preserve the original runtime; replace only the patched UPF executable.
FROM free5gc/upf@sha256:a9fa3ed6683f4390922cef122dd7c67ed6fb8674340abf7df73f7f47d47a7e04
LABEL org.opencontainers.image.source="https://github.com/free5gc/go-upf" \
      org.opencontainers.image.revision="04c1ab640350f5d354d09fac82cd7b4d66c78533+cbad64a4-nilfix" \
      research.upstream.fix="cbad64a4caa89ac99ef062e257e80e23cb9eba29"
COPY upf /free5gc/upf
