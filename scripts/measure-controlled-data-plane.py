#!/usr/bin/env python3
"""Sequential UE data-plane measurements; preserves raw results and failures."""
import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import threading
import time


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def run(args, timeout=30):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        def decode(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
        return 124, decode(exc.stdout), decode(exc.stderr) + "\nTimed out"


def kubectl(*args, timeout=30):
    return run(["kubectl", *args], timeout=timeout)


def require_output(result):
    code, stdout, stderr = result
    if code:
        raise RuntimeError(stderr or stdout)
    return stdout


def quantile(values, q):
    values = sorted(values)
    if not values:
        return None
    pos = (len(values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def stats(values):
    return {"count": len(values), "mean": statistics.mean(values) if values else None,
            "p50": quantile(values, .5), "p95": quantile(values, .95),
            "min": min(values) if values else None, "max": max(values) if values else None}


def quantity(value):
    factors = {"n": 1e-9, "u": 1e-6, "m": .001, "Ki": 1024,
               "Mi": 1024 ** 2, "Gi": 1024 ** 3, "": 1}
    match = re.fullmatch(r"([0-9.]+)(n|u|m|Ki|Mi|Gi)?", value)
    return float(match[1]) * factors[match[2] or ""] if match else None


def parse_cgroup(text):
    sections = {}
    section = None
    for line in text.splitlines():
        if line.startswith("SECTION "):
            section = line.split()[1]
            sections[section] = []
        elif section:
            sections[section].append(line)
    def mapping(name):
        return {key: int(value) for key, value in
                (line.split() for line in sections[name] if line.strip())}
    return {"memory_bytes": int(sections["memory"][0]),
            "limit_bytes": int(sections["limit"][0]),
            "memory_stat": mapping("stat"), "cpu_stat": mapping("cpu"),
            "memory_events": mapping("events")}


def stability_test(args):
    """Bounded diagnostic loads with cgroup sampling and fail-closed cutoff."""
    directory = Path(args.out_dir)
    directory.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault("KUBECONFIG", "/home/lhm/.kube/config")
    pods = json.loads(require_output(kubectl("get", "pods", "-n", args.namespace, "-o", "json")))
    def select(label, value):
        matches = [p for p in pods["items"] if p["metadata"].get("labels", {}).get(label) == value
                   and not p["metadata"].get("deletionTimestamp")
                   and p["status"].get("phase") == "Running"]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one live {label}={value} Pod")
        return matches[0]
    ue, upf = select("component", "ue"), select("nf", "upf")
    ue_cmd = ["kubectl", "-n", args.namespace, "exec", ue["metadata"]["name"], "--"]
    ip_text = require_output(run(ue_cmd + ["ip", "-4", "addr", "show", "uesimtun0"]))
    source_ip = re.search(r"inet ([0-9.]+)/", ip_text)[1]
    route = require_output(run(ue_cmd + ["ip", "route", "get", args.server, "from", source_ip]))
    if "dev uesimtun0" not in route:
        raise RuntimeError("UE source route bypasses tunnel")
    code, _, _ = run(ue_cmd + ["pgrep", "-x", "iperf3"])
    if code != 1:
        raise RuntimeError("UE already has iperf3 or process inspection failed")
    ping = run(ue_cmd + ["ping", "-I", "uesimtun0", "-c", "3", "-W", "2", args.server])
    (directory / "preflight-ping.txt").write_text(ping[1] + ping[2])
    require_output(ping)
    metadata = {"start_time": now(), "kind": "load-stability-diagnostic",
                "parameters": vars(args), "ue_pod": ue["metadata"]["name"],
                "upf_pod": upf["metadata"]["name"], "upf_uid": upf["metadata"]["uid"],
                "upf_node": upf["spec"]["nodeName"], "source_ip": source_ip, "route": route,
                "resources": [c.get("resources", {}) for c in upf["spec"]["containers"]],
                "images": [c["image"] for c in upf["spec"]["containers"]],
                "upf_initial_status": upf["status"].get("containerStatuses", []),
                "configuration_changed": False, "warmup_seconds": 0,
                "sampling": "cgroup v2 via kubectl exec; 1 second gap plus command latency"}
    (directory / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    phase, samples, rounds = ["idle"], [], []
    finish, cutoff = threading.Event(), threading.Event()
    guard = {"reason": None, "last_sample_monotonic": time.monotonic()}
    probe_script = ("echo SECTION memory; cat /sys/fs/cgroup/memory.current; "
                    "echo SECTION limit; cat /sys/fs/cgroup/memory.max; "
                    "echo SECTION stat; cat /sys/fs/cgroup/memory.stat; "
                    "echo SECTION cpu; cat /sys/fs/cgroup/cpu.stat; "
                    "echo SECTION events; cat /sys/fs/cgroup/memory.events")
    probe = ["kubectl", "-n", args.namespace, "exec", upf["metadata"]["name"],
             "--", "sh", "-c", probe_script]
    def trip(reason):
        if not cutoff.is_set():
            guard["reason"] = reason
            cutoff.set()
            print(f"{now()} SAFETY STOP {reason}", flush=True)
    def monitor():
        previous = None
        with (directory / "upf-cgroup.jsonl").open("w", buffering=1) as output:
            while not finish.is_set():
                try:
                    sample = parse_cgroup(require_output(run(probe, timeout=8)))
                    sample.update(timestamp=now(), phase=phase[0], monotonic=time.monotonic())
                    sample["memory_mib"] = sample["memory_bytes"] / 1024**2
                    if previous:
                        delta = sample["monotonic"] - previous["monotonic"]
                        sample["cpu_cores"] = (sample["cpu_stat"]["usage_usec"] -
                                               previous["cpu_stat"]["usage_usec"]) / 1e6 / delta
                    samples.append(sample)
                    output.write(json.dumps(sample) + "\n")
                    guard["last_sample_monotonic"] = sample["monotonic"]
                    threshold = min(args.memory_stop_mib * 1024**2, sample["limit_bytes"] * .5)
                    if sample["memory_bytes"] >= threshold:
                        trip(f"UPF memory {sample['memory_mib']:.1f} MiB reached guard limit")
                    if sample["memory_events"].get("oom", 0) or sample["memory_events"].get("oom_kill", 0):
                        trip("UPF cgroup reported OOM")
                    previous = sample
                except Exception as exc:
                    output.write(json.dumps({"timestamp": now(), "phase": phase[0], "error": str(exc)}) + "\n")
                    trip(f"UPF sampling failed: {exc}")
                    break
                finish.wait(1)
    def observe(seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if time.monotonic() - guard["last_sample_monotonic"] > 5:
                trip("UPF sample stale for more than 5 seconds")
            if cutoff.is_set():
                raise RuntimeError(guard["reason"])
            time.sleep(.2)
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    failure = None
    try:
        print(f"{now()} START idle {args.idle_seconds}s", flush=True)
        observe(args.idle_seconds)
        for protocol in ("tcp", "udp"):
            for rate in args.rates_mbps:
                phase[0] = f"{protocol}-{rate}M"
                if cutoff.is_set():
                    raise RuntimeError(guard["reason"])
                command = ue_cmd + ["iperf3", "-c", args.server, "-B", source_ip, "-p", "5201",
                                    "-t", str(args.duration), "-P", "1", "-b", f"{rate}M", "-J",
                                    "--get-server-output", "--connect-timeout", "5000"]
                if protocol == "udp":
                    command.extend(["-u", "-l", "1200"])
                label = phase[0]
                (directory / f"{label}.command.json").write_text(json.dumps(command) + "\n")
                row = {"phase": label, "target_mbps": rate, "started_at": now()}
                print(f"{now()} START {label} {args.duration}s", flush=True)
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                deadline = time.monotonic() + args.duration + 15
                reason = None
                while True:
                    try:
                        stdout, stderr = process.communicate(timeout=1)
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() - guard["last_sample_monotonic"] > 5:
                            trip("UPF sample stale for more than 5 seconds")
                        if cutoff.is_set() or time.monotonic() >= deadline:
                            reason = guard["reason"] or "iperf timeout"
                            run(ue_cmd + ["pkill", "-TERM", "-x", "iperf3"], timeout=8)
                            process.kill()
                            stdout, stderr = process.communicate()
                            break
                (directory / f"{label}.json").write_text(stdout)
                (directory / f"{label}.stderr.txt").write_text(stderr)
                row.update(ended_at=now(), exit_code=process.returncode)
                try:
                    if reason or process.returncode:
                        raise RuntimeError(reason or stderr or f"exit {process.returncode}")
                    result = json.loads(stdout)
                    if result.get("error"):
                        raise RuntimeError(result["error"])
                    end = result["end"]
                    received = (end.get("sum_received") or end["sum"]) if protocol == "udp" else end["sum_received"]
                    row.update(status="success", receiver_mbps=received["bits_per_second"] / 1e6)
                    if protocol == "udp":
                        row.update(lost_packets=received.get("lost_packets"), packets=received.get("packets"),
                                   loss_percent=received.get("lost_percent"), jitter_ms=received.get("jitter_ms"))
                    else:
                        row["retransmits"] = end.get("sum_sent", {}).get("retransmits")
                except (ValueError, KeyError, RuntimeError) as exc:
                    row.update(status="failed", error=str(exc))
                rounds.append(row)
                (directory / "rounds.json").write_text(json.dumps(rounds, indent=2) + "\n")
                print(f"{now()} DONE {label} {row['status']} receiver={row.get('receiver_mbps')}", flush=True)
                phase[0] = f"cooldown-{label}"
                if row["status"] == "failed":
                    run(ue_cmd + ["pkill", "-TERM", "-x", "iperf3"], timeout=8)
                    # Continue sampling without applying any new load.
                    finish.wait(min(args.cooldown_seconds, 30))
                    raise RuntimeError(row["error"])
                observe(args.cooldown_seconds)
    except Exception as exc:
        failure = str(exc)
        print(f"{now()} STOP {failure}", flush=True)
    finally:
        finish.set()
        thread.join(timeout=10)
        metadata.update(end_time=now(), failure=failure, safety_stop=guard["reason"])
        status = kubectl("get", "pod", upf["metadata"]["name"], "-n", args.namespace, "-o", "json")
        if status[0] == 0:
            current = json.loads(status[1])
            metadata["upf_final_status"] = current["status"].get("containerStatuses", [])
        (directory / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        grouped = {}
        for sample in samples:
            grouped.setdefault(sample["phase"], []).append(sample)
        summary = {"failure": failure, "safety_stop": guard["reason"], "rounds": rounds, "phases": {}}
        for name, values in grouped.items():
            summary["phases"][name] = {
                "samples": len(values), "memory_start_mib": values[0]["memory_mib"],
                "memory_end_mib": values[-1]["memory_mib"],
                "memory_peak_mib": max(v["memory_mib"] for v in values),
                "anon_end_mib": values[-1]["memory_stat"].get("anon", 0) / 1024**2,
                "cpu_cores": stats([v["cpu_cores"] for v in values if "cpu_cores" in v]),
                "oom_kill_end": values[-1]["memory_events"].get("oom_kill", 0)}
        (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2), flush=True)
    if failure:
        raise SystemExit(1)


def summarize(directory):
    rows = list(csv.DictReader((directory / "rounds.csv").open()))
    rtts = []
    for filename in sorted(directory.glob("rtt-*.txt")):
        rtts.extend(float(x) for x in re.findall(r"time=([0-9.]+) ms", filename.read_text()))
    ping_rows = [x for x in rows if x["mode"] == "rtt"]
    tx = sum(int(x["packets_tx"] or 0) for x in ping_rows)
    rx = sum(int(x["packets_rx"] or 0) for x in ping_rows)
    summary = {"generated_at": now(), "rtt_ms": stats(rtts),
               "ping": {"tx": tx, "rx": rx, "loss_percent": 100 * (tx-rx)/tx if tx else None}}
    for mode in ("tcp-up", "tcp-down", "udp-up"):
        samples = [x for x in rows if x["mode"] == mode]
        values = [float(x["receiver_mbps"]) for x in samples if x["receiver_mbps"]]
        summary[mode] = {"rounds": len(samples),
                         "successful": sum(x["status"] == "success" for x in samples),
                         "receiver_mbps": stats(values)}
        if mode == "udp-up":
            lost = sum(int(x["lost_packets"] or 0) for x in samples)
            packets = sum(int(x["packets_rx"] or 0) for x in samples)
            summary[mode].update({"jitter_ms": stats([float(x["jitter_ms"]) for x in samples if x["jitter_ms"]]),
                                  "lost_packets": lost, "packets": packets,
                                  "loss_percent": 100*lost/packets if packets else None})
    node_samples = {}
    metric_file = directory / "node-metrics.jsonl"
    if metric_file.exists():
        seen = set()
        for line in metric_file.read_text().splitlines():
            data = json.loads(line)
            for item in data.get("metrics", {}).get("items", []):
                name = item["metadata"]["name"]
                key = (name, item["timestamp"])
                if key in seen:
                    continue
                seen.add(key)
                node_samples.setdefault(name, {"cpu_cores": [], "memory_mib": []})
                node_samples[name]["cpu_cores"].append(quantity(item["usage"]["cpu"]))
                node_samples[name]["memory_mib"].append(quantity(item["usage"]["memory"]) / 1024**2)
        summary["node_resources"] = {name: {metric: stats(values) for metric, values in samples.items()}
                                     for name, samples in node_samples.items()}
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--server", default="192.168.244.128")
    parser.add_argument("--namespace", default="free5gc")
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--ping-count", type=int, default=30)
    parser.add_argument("--stability", action="store_true", help="Bounded load diagnostic, not full throughput benchmark")
    parser.add_argument("--rates-mbps", nargs="+", type=int, default=[5, 10, 20])
    parser.add_argument("--idle-seconds", type=int, default=60)
    parser.add_argument("--cooldown-seconds", type=int, default=30)
    parser.add_argument("--memory-stop-mib", type=int, default=512)
    parser.add_argument("--modes", nargs="+", choices=["rtt", "tcp-up", "tcp-down", "udp-up"],
                        default=["rtt", "tcp-up", "tcp-down", "udp-up"])
    args = parser.parse_args()
    if args.stability:
        if min(args.rates_mbps + [args.duration, args.idle_seconds, args.cooldown_seconds, args.memory_stop_mib]) <= 0:
            parser.error("Stability diagnostic parameters must be positive")
        stability_test(args)
        return
    directory = Path(args.out_dir)
    directory.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault("KUBECONFIG", "/home/lhm/.kube/config")
    pods = json.loads(require_output(kubectl("get", "pods", "-n", args.namespace, "-o", "json")))
    candidates = [p for p in pods["items"] if p["metadata"].get("labels", {}).get("component") == "ue"
                  and not p["metadata"].get("deletionTimestamp") and p["status"].get("phase") == "Running"]
    if len(candidates) != 1:
        raise RuntimeError("Expected exactly one live UE Pod")
    ue = candidates[0]["metadata"]["name"]
    command_prefix = ["kubectl", "-n", args.namespace, "exec", ue, "--"]
    ip_text = require_output(run(command_prefix + ["ip", "-4", "addr", "show", "uesimtun0"]))
    source_ip = re.search(r"inet ([0-9.]+)/", ip_text)[1]
    route = require_output(run(command_prefix + ["ip", "route", "get", args.server, "from", source_ip]))
    if "dev uesimtun0" not in route:
        raise RuntimeError("Source route does not use uesimtun0")
    # Save only non-secret deployment information, not subscriber keys/configs.
    snapshot = [{"name": p["metadata"]["name"], "uid": p["metadata"]["uid"],
                 "node": p["spec"]["nodeName"], "pod_ip": p["status"].get("podIP"),
                 "containers": [{"name": c["name"], "image": c["image"], "resources": c.get("resources", {})}
                                for c in p["spec"]["containers"]],
                 "status": p["status"].get("containerStatuses", [])} for p in pods["items"]]
    metadata = {"start_time": now(), "server": args.server, "ue_pod": ue,
                "source_ip": source_ip, "route": route, "parameters": vars(args),
                "tcp_streams": 1, "udp_target_mbps": 20, "udp_payload_bytes": 1200,
                "ping_interval_seconds": .2, "placement": "core-upf-vm2",
                "pods": snapshot, "artificial_delay": False}
    (directory / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    stop = threading.Event()
    phase = ["preflight"]

    def monitor():
        with (directory / "node-metrics.jsonl").open("a", buffering=1) as output:
            while not stop.is_set():
                code, stdout, stderr = kubectl("get", "--raw", "/apis/metrics.k8s.io/v1beta1/nodes", timeout=12)
                sample = {"collected_at": now(), "phase": phase[0]}
                try:
                    sample["metrics"] = json.loads(stdout) if not code else {}
                except ValueError:
                    sample["metrics"] = {}
                if code:
                    sample["error"] = stderr
                output.write(json.dumps(sample) + "\n")
                stop.wait(10)

    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    fields = ["mode", "round", "started_at", "ended_at", "status", "exit_code",
              "receiver_mbps", "jitter_ms", "lost_packets", "packets_tx", "packets_rx",
              "loss_percent", "tcp_retransmits", "error"]
    try:
        with (directory / "rounds.csv").open("w", newline="", buffering=1) as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            # Connectivity only; excluded from the formal RTT dataset.
            code, stdout, stderr = run(command_prefix + ["ping", "-I", "uesimtun0", "-c", "5", "-i", "0.2", "-W", "2", args.server])
            (directory / "preflight-ping.txt").write_text(stdout + stderr)
            if code:
                raise RuntimeError("Preflight connectivity failed; formal measurements were not started")
            for mode in args.modes:
                for round_number in range(1, args.rounds + 1):
                    phase[0] = f"{mode}-{round_number}"
                    print(f"{now()} START {phase[0]}", flush=True)
                    row = dict.fromkeys(fields, "")
                    row.update(mode=mode, round=round_number, started_at=now())
                    if mode == "rtt":
                        command = command_prefix + ["ping", "-I", "uesimtun0", "-c", str(args.ping_count),
                                                     "-i", "0.2", "-W", "2", args.server]
                        suffix = "txt"
                        timeout = args.ping_count * .2 + 15
                    else:
                        command = command_prefix + ["iperf3", "-c", args.server, "-B", source_ip, "-p", "5201",
                                                     "-t", str(args.duration), "-O", str(args.warmup), "-P", "1", "-J",
                                                     "--get-server-output", "--connect-timeout", "5000"]
                        if mode == "tcp-down":
                            command.append("-R")
                        if mode == "udp-up":
                            command.extend(["-u", "-b", "20M", "-l", "1200"])
                        suffix = "json"
                        timeout = args.duration + args.warmup + 25
                    (directory / f"{mode}-{round_number:02d}.command.json").write_text(json.dumps(command) + "\n")
                    code, stdout, stderr = run(command, timeout=timeout)
                    (directory / f"{mode}-{round_number:02d}.{suffix}").write_text(stdout)
                    (directory / f"{mode}-{round_number:02d}.stderr.txt").write_text(stderr)
                    row.update(ended_at=now(), exit_code=code, status="success" if code == 0 else "failed")
                    if mode == "rtt":
                        match = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received, ([0-9.]+)% packet loss", stdout)
                        if match:
                            row.update(packets_tx=match[1], packets_rx=match[2], loss_percent=match[3])
                    else:
                        try:
                            data = json.loads(stdout)
                            if data.get("error"):
                                raise ValueError(data["error"])
                            end = data["end"]
                            if mode == "udp-up":
                                # iperf3 3.9 exposes receiver statistics as end.sum;
                                # newer versions additionally expose sum_received.
                                received = end.get("sum_received") or end["sum"]
                                row.update(jitter_ms=received.get("jitter_ms", ""),
                                           lost_packets=received.get("lost_packets", ""),
                                           packets_rx=received.get("packets", ""),
                                           loss_percent=received.get("lost_percent", ""))
                            else:
                                received = end["sum_received"]
                                row["tcp_retransmits"] = end.get("sum_sent", {}).get("retransmits", "")
                            row["receiver_mbps"] = received["bits_per_second"] / 1e6
                        except (ValueError, KeyError) as exc:
                            row.update(status="failed", error=str(exc))
                    if code:
                        row["error"] = stderr.strip() or row["error"] or f"Exit code {code}"
                    writer.writerow(row)
                    output.flush()
                    print(f"{now()} DONE {phase[0]} {row['status']} receiver_mbps={row['receiver_mbps']} loss={row['loss_percent']}", flush=True)
                    if row["status"] == "failed":
                        # A timeout may indicate NF failure; never send more load
                        # into a broken session or silently replace failed rounds.
                        if code == 124 and mode != "rtt":
                            run(command_prefix + ["pkill", "-TERM", "-x", "iperf3"], timeout=10)
                        raise RuntimeError(f"Measurement stopped after failure: {phase[0]}: {row['error']}")
                    time.sleep(2)
    finally:
        stop.set()
        monitor_thread.join(timeout=15)
        metadata["end_time"] = now()
        (directory / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        if (directory / "rounds.csv").exists():
            print(json.dumps(summarize(directory), indent=2), flush=True)


if __name__ == "__main__":
    main()
