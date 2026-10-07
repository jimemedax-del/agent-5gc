#!/usr/bin/env bash
set -Eeuo pipefail

K3S_VERSION="${K3S_VERSION:-v1.30.14+k3s1}"
HELM_VERSION="${HELM_VERSION:-v3.17.2}"
GTP5G_VERSION="${GTP5G_VERSION:-v0.9.5}"
GTP5G_COMMIT="973d001b25832c5a8e8d34f6381eb0c705fb523d"
NODE_IFACE="${NODE_IFACE:-ens33}"
INSTALL_GTP5G="${INSTALL_GTP5G:-0}"
installer=""
helm_tmp=""

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REGISTRIES_FILE="${REPO_ROOT}/infra/k3s-registries.yaml"

usage() {
  cat <<'EOF'
Usage:
  sudo env NODE_IP=<VMware-LAN-IP> [INSTALL_GTP5G=1] ./scripts/bootstrap-k3s-node.sh server
  sudo env NODE_IP=<VMware-LAN-IP> K3S_URL=https://<server-LAN-IP>:6443 \
    K3S_TOKEN=<token> [INSTALL_GTP5G=1] ./scripts/bootstrap-k3s-node.sh agent

The script is intended for a clean Ubuntu 22.04 node. Tailscale is only for
management; NODE_IP must be the VMware LAN address.
EOF
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

cleanup() {
  [[ -z "${installer}" ]] || rm -f -- "${installer}"
  [[ -z "${helm_tmp}" ]] || rm -rf -- "${helm_tmp}"
}
trap cleanup EXIT

[[ $# -eq 1 ]] || { usage; exit 2; }
ROLE="$1"
[[ "${ROLE}" == "server" || "${ROLE}" == "agent" ]] || { usage; exit 2; }
[[ ${EUID} -eq 0 ]] || fail "run with sudo"
: "${NODE_IP:?Set NODE_IP to the VMware LAN address of this node}"
[[ -f "${REGISTRIES_FILE}" ]] || fail "missing ${REGISTRIES_FILE}"

if [[ "${ROLE}" == "agent" ]]; then
  : "${K3S_URL:?Set K3S_URL, for example https://192.168.244.128:6443}"
  : "${K3S_TOKEN:?Set K3S_TOKEN from the server; never commit it}"
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git python3 build-essential "linux-headers-$(uname -r)" gcc-12

install_gtp5g() {
  local source_root="/opt/free5gc-src"
  local source_dir="${source_root}/gtp5g-${GTP5G_VERSION}"

  if modinfo gtp5g >/dev/null 2>&1; then
    local installed_version
    installed_version="$(modinfo -F version gtp5g || true)"
    local installed_vermagic
    installed_vermagic="$(modinfo -F vermagic gtp5g || true)"
    if [[ "${installed_version}" == "${GTP5G_VERSION#v}" && "${installed_vermagic}" == "$(uname -r)"* ]]; then
      modprobe gtp5g
      echo "gtp5g ${installed_version} already matches $(uname -r)"
      return
    fi
    fail "an incompatible gtp5g module is installed: version=${installed_version}, vermagic=${installed_vermagic}"
  fi

  install -d -m 0755 "${source_root}"
  if [[ ! -d "${source_dir}/.git" ]]; then
    git clone --branch "${GTP5G_VERSION}" --depth 1 https://github.com/free5gc/gtp5g.git "${source_dir}"
  fi
  [[ "$(git -C "${source_dir}" rev-parse HEAD)" == "${GTP5G_COMMIT}" ]] || \
    fail "gtp5g source is not the expected commit ${GTP5G_COMMIT}"

  make -C "${source_dir}" clean
  make -C "${source_dir}" -j "$(nproc)" CC=gcc-12
  make -C "${source_dir}" install
  modprobe gtp5g
  [[ "$(modinfo -F version gtp5g)" == "${GTP5G_VERSION#v}" ]] || fail "gtp5g version verification failed"
  lsmod | grep -q '^gtp5g' || fail "gtp5g did not load"
}

if [[ "${INSTALL_GTP5G}" == "1" ]]; then
  install_gtp5g
fi

install -d -m 0755 /etc/rancher/k3s
install -m 0644 "${REGISTRIES_FILE}" /etc/rancher/k3s/registries.yaml

if [[ "${ROLE}" == "server" && -f /etc/systemd/system/k3s-agent.service ]]; then
  fail "this node is already configured as a k3s agent"
fi
if [[ "${ROLE}" == "agent" && -f /etc/systemd/system/k3s.service ]]; then
  fail "this node is already configured as a k3s server"
fi

if command -v k3s >/dev/null 2>&1; then
  k3s --version | grep -q "${K3S_VERSION}" || fail "existing k3s version differs from ${K3S_VERSION}"
  echo "k3s ${K3S_VERSION} is already installed; installation skipped"
else
  installer="$(mktemp)"
  curl -fsSL https://get.k3s.io -o "${installer}"
  chmod 0700 "${installer}"

  common_args=(
    --node-ip "${NODE_IP}"
    --flannel-iface "${NODE_IFACE}"
    --kubelet-arg fail-swap-on=false
    --kubelet-arg allowed-unsafe-sysctls=net.ipv4.ip_forward
  )

  if [[ "${ROLE}" == "server" ]]; then
    INSTALL_K3S_VERSION="${K3S_VERSION}" sh "${installer}" server \
      --disable traefik \
      --disable servicelb \
      --advertise-address "${NODE_IP}" \
      --tls-san "${NODE_IP}" \
      "${common_args[@]}"
  else
    K3S_URL="${K3S_URL}" K3S_TOKEN="${K3S_TOKEN}" INSTALL_K3S_VERSION="${K3S_VERSION}" \
      sh "${installer}" agent "${common_args[@]}"
  fi
fi

if [[ "${ROLE}" == "server" ]]; then
  target_user="${SUDO_USER:-root}"
  target_user_home="$(getent passwd "${target_user}" | cut -d: -f6)"
  [[ -n "${target_user_home}" ]] || fail "cannot determine home directory for ${target_user}"
  install -d -m 0700 -o "${target_user}" -g "${target_user}" "${target_user_home}/.kube"
  install -m 0600 -o "${target_user}" -g "${target_user}" \
    /etc/rancher/k3s/k3s.yaml "${target_user_home}/.kube/config"

  if ! command -v helm >/dev/null 2>&1 || ! helm version --short | grep -q "${HELM_VERSION}"; then
    helm_tmp="$(mktemp -d)"
    helm_archive="helm-${HELM_VERSION}-linux-amd64.tar.gz"
    curl -fsSL "https://get.helm.sh/${helm_archive}" -o "${helm_tmp}/${helm_archive}"
    curl -fsSL "https://get.helm.sh/${helm_archive}.sha256sum" -o "${helm_tmp}/${helm_archive}.sha256sum"
    expected_hash="$(tr -d '[:space:]' < "${helm_tmp}/${helm_archive}.sha256sum")"
    actual_hash="$(sha256sum "${helm_tmp}/${helm_archive}" | awk '{print $1}')"
    [[ "${expected_hash}" == "${actual_hash}" ]] || fail "Helm archive checksum mismatch"
    tar -xzf "${helm_tmp}/${helm_archive}" -C "${helm_tmp}"
    install -m 0755 "${helm_tmp}/linux-amd64/helm" /usr/local/bin/helm
  fi
fi

systemctl is-active --quiet "$([[ "${ROLE}" == "server" ]] && echo k3s || echo k3s-agent)" || \
  fail "k3s service is not active"

echo "Node bootstrap complete: role=${ROLE}, node-ip=${NODE_IP}, k3s=${K3S_VERSION}, gtp5g=${INSTALL_GTP5G}"
