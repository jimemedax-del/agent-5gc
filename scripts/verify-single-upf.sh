#!/usr/bin/env bash
set -Eeuo pipefail

NAMESPACE="${NAMESPACE:-free5gc}"
CORE_RELEASE="${CORE_RELEASE:-free5gc-helm}"
RAN_RELEASE="${RAN_RELEASE:-ueransim}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-10m}"

fail() {
  echo "VERIFY FAILED: $*" >&2
  exit 1
}

for command_name in kubectl helm grep python3; do
  command -v "${command_name}" >/dev/null 2>&1 || fail "missing command: ${command_name}"
done

[[ "$(helm status "${CORE_RELEASE}" -n "${NAMESPACE}" -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)["info"]["status"])')" == "deployed" ]] || \
  fail "core Helm release is not deployed"
[[ "$(helm status "${RAN_RELEASE}" -n "${NAMESPACE}" -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)["info"]["status"])')" == "deployed" ]] || \
  fail "UERANSIM Helm release is not deployed"

kubectl -n "${NAMESPACE}" wait --for=condition=Available deployment --all --timeout="${WAIT_TIMEOUT}"

non_running="$(kubectl -n "${NAMESPACE}" get pods --no-headers | awk '$3 != "Running" && $3 != "Completed" {print $1 ":" $3}')"
[[ -z "${non_running}" ]] || fail "non-running pods: ${non_running}"

smf_log="$(kubectl -n "${NAMESPACE}" logs deploy/free5gc-helm-free5gc-smf-smf)"
grep -Eq 'PFCP Association Setup Accepted|handleAssociationSetupRequest|New node' <<<"${smf_log}" || \
  fail "SMF log does not show a successful PFCP association"

gnb_log="$(kubectl -n "${NAMESPACE}" logs deploy/ueransim-gnb)"
grep -q 'NG Setup procedure is successful' <<<"${gnb_log}" || fail "gNB log does not show successful NG Setup"

ue_log="$(kubectl -n "${NAMESPACE}" logs deploy/ueransim-ue)"
grep -q 'Initial Registration is successful' <<<"${ue_log}" || fail "UE registration success not found"
grep -q 'PDU Session establishment is successful' <<<"${ue_log}" || fail "PDU Session success not found"

kubectl -n "${NAMESPACE}" exec deploy/ueransim-ue -- ip -br addr show uesimtun0
kubectl -n "${NAMESPACE}" exec deploy/ueransim-ue -- ping -I uesimtun0 -c 3 -W 4 1.1.1.1

node_count="$(kubectl get nodes --no-headers | wc -l | tr -d ' ')"
if [[ "${node_count}" == "1" ]] && command -v lsmod >/dev/null 2>&1; then
  lsmod | grep -q '^gtp5g' || fail "single node does not have gtp5g loaded"
fi

echo "Placement snapshot:"
kubectl -n "${NAMESPACE}" get pods -o wide
echo "Single-UPF end-to-end verification passed"
