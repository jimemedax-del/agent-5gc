#!/usr/bin/env bash
# Repeated functional-baseline measurement for the current three-node setup.
# It deliberately does not inject tc/netem delay; results are availability and
# session-establishment baseline data, not an edge-latency claim.
set -uo pipefail

NAMESPACE="${NAMESPACE:-free5gc}"
UE_DEPLOYMENT="${UE_DEPLOYMENT:-ueransim-ue}"
COUNT="${COUNT:-20}"
PDU_TIMEOUT_SECONDS="${PDU_TIMEOUT_SECONDS:-60}"
PING_COUNT="${PING_COUNT:-5}"
PING_TARGET="${PING_TARGET:-1.1.1.1}"
OUT_DIR="${OUT_DIR:-$PWD/results}"

command -v kubectl >/dev/null || { echo 'kubectl is required' >&2; exit 1; }
command -v date >/dev/null || { echo 'date is required' >&2; exit 1; }
mkdir -p "$OUT_DIR"

run_id="$(date +%Y%m%d-%H%M%S)"
result_file="$OUT_DIR/multinode-baseline-$run_id.tsv"
printf 'round\tpod\tregistration_ms\tpdu_session_ms\ttun_ready\tping_tx\tping_rx\tping_loss_percent\tresult\n' >"$result_file"

timestamp_ms() {
  # UERANSIM lines start with: [YYYY-MM-DD HH:MM:SS.mmm]
  local line="$1" timestamp
  timestamp="$(sed -nE 's/^\[([^]]+)\].*/\1/p' <<<"$line")"
  [[ -n "$timestamp" ]] && date -d "$timestamp" +%s%3N 2>/dev/null
}

latest_ue_pod() {
  kubectl get pods -n "$NAMESPACE" --sort-by=.metadata.creationTimestamp \
    --no-headers | awk '$3 == "Running" && $1 ~ /^ueransim-ue-/ {pod=$1} END {print pod}'
}

for round in $(seq 1 "$COUNT"); do
  echo "[$(date '+%F %T')] round $round/$COUNT"
  kubectl rollout restart -n "$NAMESPACE" "deployment/$UE_DEPLOYMENT" >/dev/null

  if ! kubectl rollout status -n "$NAMESPACE" "deployment/$UE_DEPLOYMENT" --timeout=120s >/dev/null; then
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$round" '-' '-' '-' 'false' '-' '-' '-' 'rollout_failed' >>"$result_file"
    continue
  fi

  pod="$(latest_ue_pod)"
  if [[ -z "$pod" ]]; then
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$round" '-' '-' '-' 'false' '-' '-' '-' 'pod_not_found' >>"$result_file"
    continue
  fi

  log_file="$OUT_DIR/${run_id}-round-${round}-${pod}.log"
  pdu_ok=false
  for _ in $(seq 1 "$PDU_TIMEOUT_SECONDS"); do
    kubectl logs -n "$NAMESPACE" "$pod" >"$log_file" 2>&1 || true
    if grep -q 'PDU Session establishment is successful' "$log_file"; then
      pdu_ok=true
      break
    fi
    sleep 1
  done

  registration_line="$(grep -m1 'Initial Registration is successful' "$log_file" || true)"
  registration_start_line="$(grep -m1 'Sending Initial Registration' "$log_file" || true)"
  pdu_start_line="$(grep -m1 'Sending PDU Session Establishment Request' "$log_file" || true)"
  pdu_success_line="$(grep -m1 'PDU Session establishment is successful' "$log_file" || true)"
  registration_start_ms="$(timestamp_ms "$registration_start_line" || true)"
  registration_end_ms="$(timestamp_ms "$registration_line" || true)"
  pdu_start_ms="$(timestamp_ms "$pdu_start_line" || true)"
  pdu_end_ms="$(timestamp_ms "$pdu_success_line" || true)"
  registration_ms='-'
  pdu_session_ms='-'
  [[ "$registration_start_ms" =~ ^[0-9]+$ && "$registration_end_ms" =~ ^[0-9]+$ ]] && registration_ms=$((registration_end_ms - registration_start_ms))
  [[ "$pdu_start_ms" =~ ^[0-9]+$ && "$pdu_end_ms" =~ ^[0-9]+$ ]] && pdu_session_ms=$((pdu_end_ms - pdu_start_ms))

  tun_ready=false
  if kubectl exec -n "$NAMESPACE" "$pod" -- ip link show uesimtun0 >/dev/null 2>&1; then
    tun_ready=true
  fi

  ping_tx='-'; ping_rx='-'; ping_loss='-'
  if [[ "$tun_ready" == true ]]; then
    ping_output="$(kubectl exec -n "$NAMESPACE" "$pod" -- ping -I uesimtun0 -c "$PING_COUNT" -W 3 "$PING_TARGET" 2>&1 || true)"
    ping_summary="$(grep -E '[0-9]+ packets transmitted' <<<"$ping_output" | tail -n1 || true)"
    ping_tx="$(sed -nE 's/([0-9]+) packets transmitted.*/\1/p' <<<"$ping_summary")"
    ping_rx="$(sed -nE 's/.* ([0-9]+) (packets )?received.*/\1/p' <<<"$ping_summary")"
    ping_loss="$(sed -nE 's/.* ([0-9.]+)% packet loss.*/\1/p' <<<"$ping_summary")"
  fi

  result='failed'
  [[ "$pdu_ok" == true && "$tun_ready" == true ]] && result='success'
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$round" "$pod" "$registration_ms" "$pdu_session_ms" "$tun_ready" \
    "${ping_tx:--}" "${ping_rx:--}" "${ping_loss:--}" "$result" >>"$result_file"
done

echo "Results: $result_file"
column -t -s $'\t' "$result_file" 2>/dev/null || cat "$result_file"
