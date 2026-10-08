#!/usr/bin/env bash
# Run as root on VM1, with the checked-in systemd unit as the sole argument.
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run as root on VM1' >&2; exit 1; }
unit_file="${1:?Pass the free5gc-iperf3.service file}"
[[ -f "$unit_file" ]] || { echo 'Unit file missing' >&2; exit 1; }
ip -4 addr show | grep -q 'inet 192.168.244.128/' || {
  echo 'VM1 address 192.168.244.128 is missing; review service bind address' >&2
  exit 1
}
export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::Retries=2 update
apt-get install -y --no-install-recommends iperf3
install -m 0644 "$unit_file" /etc/systemd/system/free5gc-iperf3.service
systemctl daemon-reload
systemctl enable --now free5gc-iperf3.service
# UFW is left in its current enabled/disabled state. Allow only the two
# worker LAN addresses and their Pod subnets, for TCP control/data and UDP.
if command -v ufw >/dev/null 2>&1; then
  for source_net in 192.168.244.129/32 192.168.244.130/32 10.42.1.0/24 10.42.2.0/24; do
    for protocol in tcp udp; do
      ufw allow from "$source_net" to 192.168.244.128 port 5201 proto "$protocol"
    done
  done
fi
systemctl is-active free5gc-iperf3.service
ss -lntp 'sport = :5201'
iperf3 --version
