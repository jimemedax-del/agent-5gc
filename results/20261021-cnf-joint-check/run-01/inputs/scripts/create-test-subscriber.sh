#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

WEBUI_URL="${WEBUI_URL:-http://127.0.0.1:30500}"
WEBUI_USERNAME="${WEBUI_USERNAME:-admin}"
WEBUI_PASSWORD="${WEBUI_PASSWORD:-free5gc}"
SUBSCRIBER_FILE="${1:-${REPO_ROOT}/infra/free5gc-test-subscriber-ue1.json}"

[[ -f "${SUBSCRIBER_FILE}" ]] || { echo "Subscriber file not found: ${SUBSCRIBER_FILE}" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }

export WEBUI_USERNAME WEBUI_PASSWORD
python3 - "${WEBUI_URL%/}" "${SUBSCRIBER_FILE}" <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request

base_url, subscriber_path = sys.argv[1:]
username = os.environ["WEBUI_USERNAME"]
password = os.environ["WEBUI_PASSWORD"]

with open(subscriber_path, "r", encoding="utf-8") as stream:
    desired = json.load(stream)

ue_id = desired["ueId"]
plmn_id = desired["plmnID"]
subscriber_url = f"{base_url}/api/subscriber/{ue_id}/{plmn_id}"

def request(method, url, body=None, token=None, allowed_statuses=()):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Token"] = token
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            parsed = json.loads(raw) if raw else None
            return response.status, parsed
    except urllib.error.HTTPError as exc:
        if exc.code in allowed_statuses:
            return exc.code, None
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} failed: HTTP {exc.code}: {detail}") from exc

_, login = request("POST", f"{base_url}/api/login", {"username": username, "password": password})
token = login["access_token"]
_, existing = request("GET", subscriber_url, token=token, allowed_statuses=(404,))

def auth_signature(value):
    value = value if isinstance(value, dict) else {}
    auth = value.get("AuthenticationSubscription") or {}
    key = auth.get("permanentKey") or {}
    opc = auth.get("opc") or {}
    return (
        value.get("ueId"),
        value.get("plmnID"),
        key.get("permanentKeyValue"),
        opc.get("opcValue"),
        auth.get("authenticationManagementField"),
    )

def structurally_complete(value):
    if not isinstance(value, dict):
        return False
    return all([
        auth_signature(value)[2],
        auth_signature(value)[3],
        value.get("AccessAndMobilitySubscriptionData"),
        value.get("SessionManagementSubscriptionData"),
        value.get("SmfSelectionSubscriptionData"),
    ])

if structurally_complete(existing):
    if auth_signature(existing) != auth_signature(desired):
        raise RuntimeError(f"subscriber {ue_id} already exists with different identity data; refusing to overwrite")
    print(f"Subscriber already exists and matches: {ue_id}/{plmn_id}")
    raise SystemExit(0)

status, _ = request("POST", subscriber_url, desired, token=token)
_, saved = request("GET", subscriber_url, token=token)
if not structurally_complete(saved) or auth_signature(saved) != auth_signature(desired):
    raise RuntimeError(f"subscriber verification failed after HTTP {status}")
print(f"Subscriber created and verified: {ue_id}/{plmn_id} (HTTP {status})")
PY
