#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

BASE_COMMIT="0d0b4b392bbb1b099acb9a1b37c39e0647ff6d4c"
CHART_REPOSITORY="https://github.com/free5gc/free5gc-helm.git"
CHART_WORKDIR="${CHART_WORKDIR:-${REPO_ROOT}/.work/free5gc-helm-v4.2.2}"
PATCH_FILE="${REPO_ROOT}/infra/patches/free5gc-helm-v4.2.2-single-upf.patch"
CORE_VALUES="${REPO_ROOT}/infra/free5gc-single-upf-values.yaml"
RAN_VALUES="${REPO_ROOT}/infra/ueransim-single-node-values.yaml"
NAMESPACE="${NAMESPACE:-free5gc}"
CORE_RELEASE="${CORE_RELEASE:-free5gc-helm}"
RAN_RELEASE="${RAN_RELEASE:-ueransim}"
LOCAL_WEBUI_PORT="${LOCAL_WEBUI_PORT:-15000}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

for command_name in git helm kubectl curl python3; do
  command -v "${command_name}" >/dev/null 2>&1 || fail "missing command: ${command_name}"
done
for required_file in "${PATCH_FILE}" "${CORE_VALUES}" "${RAN_VALUES}"; do
  [[ -f "${required_file}" ]] || fail "missing required file: ${required_file}"
done

if [[ ! -d "${CHART_WORKDIR}/.git" ]]; then
  mkdir -p "$(dirname -- "${CHART_WORKDIR}")"
  git clone "${CHART_REPOSITORY}" "${CHART_WORKDIR}"
  git -C "${CHART_WORKDIR}" checkout --detach "${BASE_COMMIT}"
fi

[[ "$(git -C "${CHART_WORKDIR}" rev-parse HEAD)" == "${BASE_COMMIT}" ]] || \
  fail "Chart checkout is not at ${BASE_COMMIT}"

if git -C "${CHART_WORKDIR}" apply --reverse --check "${PATCH_FILE}" >/dev/null 2>&1; then
  echo "Chart patch already applied"
elif git -C "${CHART_WORKDIR}" apply --check "${PATCH_FILE}"; then
  git -C "${CHART_WORKDIR}" apply "${PATCH_FILE}"
else
  fail "Chart checkout contains changes incompatible with the controlled patch"
fi

helm lint "${CHART_WORKDIR}/charts/free5gc" -f "${CORE_VALUES}"
helm lint "${CHART_WORKDIR}/charts/ueransim" -f "${RAN_VALUES}"

kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install "${CORE_RELEASE}" "${CHART_WORKDIR}/charts/free5gc" \
  -n "${NAMESPACE}" \
  -f "${CORE_VALUES}" \
  --atomic --wait --timeout 15m --history-max 10

kubectl -n "${NAMESPACE}" rollout status deploy/free5gc-helm-free5gc-webui-webui --timeout=5m

port_forward_log="$(mktemp)"
port_forward_pid=""
cleanup() {
  if [[ -n "${port_forward_pid}" ]]; then
    kill "${port_forward_pid}" >/dev/null 2>&1 || true
    wait "${port_forward_pid}" >/dev/null 2>&1 || true
  fi
  rm -f "${port_forward_log}"
}
trap cleanup EXIT

kubectl -n "${NAMESPACE}" port-forward service/webui-service \
  "${LOCAL_WEBUI_PORT}:5000" >"${port_forward_log}" 2>&1 &
port_forward_pid="$!"

for _ in {1..30}; do
  if curl -fsS "http://127.0.0.1:${LOCAL_WEBUI_PORT}/" >/dev/null; then
    break
  fi
  sleep 1
done
curl -fsS "http://127.0.0.1:${LOCAL_WEBUI_PORT}/" >/dev/null || \
  fail "WebUI port-forward did not become ready; see ${port_forward_log}"

WEBUI_URL="http://127.0.0.1:${LOCAL_WEBUI_PORT}" \
  "${SCRIPT_DIR}/create-test-subscriber.sh"

kill "${port_forward_pid}" >/dev/null 2>&1 || true
wait "${port_forward_pid}" >/dev/null 2>&1 || true
port_forward_pid=""

helm upgrade --install "${RAN_RELEASE}" "${CHART_WORKDIR}/charts/ueransim" \
  -n "${NAMESPACE}" \
  -f "${RAN_VALUES}" \
  --atomic --wait --timeout 10m --history-max 10

"${SCRIPT_DIR}/verify-single-upf.sh"
