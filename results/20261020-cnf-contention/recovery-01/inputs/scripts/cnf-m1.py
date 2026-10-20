#!/usr/bin/env python3
"""Fixed R2.S2 M0/M1 helpers; never upgrade or restart existing 5GC workloads."""
import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / 'infra/cnfs/m1-proxy.yaml'
LOCK = ROOT / 'infra/cnfs/m1-images.json'
NS = 'cnf-lab'
CORE = 'free5gc'
PORT = 31201
LOG_DRAIN_SECONDS = 15  # Covers the observed ~8s buffered stdout access-log delay.


class PendingProxyLogs(RuntimeError):
    """The closed control/data connections have not both reached the log stream."""


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def run(args, timeout=30, required=True):
    result = subprocess.run([str(x) for x in args], capture_output=True,
                            text=True, timeout=timeout)
    if required and result.returncode:
        raise RuntimeError(f'{args[0]} failed ({result.returncode}): {result.stderr.strip()[:800]}')
    return result


def kube(*args, **kwargs):
    return run(['kubectl', *args], **kwargs)


def save(out, name, value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    (out / name).write_text(text, encoding='utf-8')


def config():
    docs = list(yaml.safe_load_all(MANIFEST.read_text(encoding='utf-8')))
    lock = json.loads(LOCK.read_text(encoding='utf-8'))
    for doc in docs:
        if doc['kind'] != 'Namespace' and doc['metadata']['namespace'] != NS:
            raise ValueError('All M1 resources must belong to cnf-lab')
    workloads = {d['metadata']['name']: d for d in docs if d['kind'] == 'Deployment'}
    for component, name in [('envoy', 'cnf-m1-envoy'), ('backend', 'cnf-m1-backend')]:
        container = workloads[name]['spec']['template']['spec']['containers'][0]
        if container['image'] != lock['images'][component]['image']:
            raise ValueError(f'Image does not match M1 lock: {name}')
    return docs, lock


def snapshot():
    """Save identity/state without environment variables or service account tokens."""
    result = {}
    for namespace in [CORE, NS]:
        pods = json.loads(kube('-n', namespace, 'get', 'pods', '-o', 'json').stdout)['items']
        result[namespace] = {}
        for pod in pods:
            if pod['metadata'].get('deletionTimestamp'):
                raise RuntimeError('Pod is terminating; wait for a stable baseline')
            statuses = pod['status'].get('containerStatuses', [])
            result[namespace][pod['metadata']['name']] = {
                'uid': pod['metadata']['uid'], 'node': pod['spec']['nodeName'],
                'ip': pod['status'].get('podIP'), 'phase': pod['status']['phase'],
                'containers': [{key: c.get(key) for key in
                                ['name', 'image', 'imageID', 'containerID', 'ready',
                                 'restartCount', 'state', 'lastState']} | {
                                     'specImage': next(x['image'] for x in pod['spec']['containers']
                                                       if x['name'] == c['name'])} for c in statuses],
            }
    return result


def identity(state):
    return {(namespace, name): (pod['uid'], tuple(
        (c['name'], c['containerID'], c['restartCount']) for c in pod['containers']))
        for namespace, pods in state.items() for name, pod in pods.items()}


def assert_steady(before, after):
    if identity(before) != identity(after):
        raise RuntimeError('Pod identity, container identity or restart count changed')
    for pods in after.values():
        for pod in pods.values():
            if pod['phase'] != 'Running' or not pod['containers'] or not all(
                    c['ready'] and 'running' in c['state'] for c in pod['containers']):
                raise RuntimeError('A monitored Pod is no longer Running/Ready')


def one_pod(state, namespace, prefix):
    matches = [(name, item) for name, item in state[namespace].items() if name.startswith(prefix + '-')]
    if len(matches) != 1:
        raise RuntimeError(f'Expected one Pod for {namespace}/{prefix}')
    return matches[0]


def baseline(out, prefix):
    report = out / prefix
    result = run([sys.executable, ROOT / 'scripts/free5gc-profile.py', 'verify', '--out', report],
                 timeout=180, required=False)
    save(out, prefix + '.stdout.txt', result.stdout)
    save(out, prefix + '.stderr.txt', result.stderr)
    if result.returncode:
        raise RuntimeError('Strict 5GC verify failed; see saved baseline output')
    state = snapshot()
    assert_steady(state, state)
    save(out, prefix + '-pods.json', state)
    return state


def target_node():
    nodes = json.loads(kube('get', 'nodes', '-l', '5gc.free5gc.org/plane=control', '-o', 'json').stdout)['items']
    if len(nodes) != 1:
        raise RuntimeError('Expected exactly one control-role node')
    node = nodes[0]
    target = next(a['address'] for a in node['status']['addresses'] if a['type'] == 'InternalIP')
    ipaddress.IPv4Address(target)
    return node, target


def ue_path(out, state, target):
    ue, _ = one_pod(state, CORE, 'ueransim-ue')
    address = kube('-n', CORE, 'exec', ue, '--', 'ip', '-j', '-4', 'addr', 'show', 'uesimtun0').stdout
    addresses = json.loads(address)
    source = next(a['local'] for d in addresses for a in d['addr_info'] if a['family'] == 'inet')
    ipaddress.IPv4Address(source)
    route = kube('-n', CORE, 'exec', ue, '--', 'ip', '-4', 'route', 'get', target, 'from', source).stdout
    save(out, 'ue-address.json', address)
    save(out, 'ue-route.txt', route)
    if 'dev uesimtun0' not in route:
        raise RuntimeError('UE source route does not use uesimtun0')
    return ue, source


def deploy(out, docs, lock):
    baseline(out, 'before-deploy')
    node, _ = target_node()
    required_image = lock['images']['backend']['image']
    images = {name for image in node['status'].get('images', []) for name in image['names']}
    if required_image not in images:
        raise RuntimeError('VM1 needs the existing UE image imported with its Digest alias; see CNF module')
    existing = json.loads(kube('get', 'services', '-A', '-o', 'json').stdout)['items']
    for service in existing:
        for port in service['spec'].get('ports', []):
            if port.get('nodePort') == PORT:
                raise RuntimeError('NodePort 31201 is already allocated; do not replace an existing service')
    ns = kube('get', 'namespace', NS, '--ignore-not-found', '-o', 'name').stdout.strip()
    if ns:
        raise RuntimeError('cnf-lab already exists; inspect it manually before applying M1 again')
    # A dry-run Namespace is not persisted. Create it before validating namespaced resources.
    # If validation fails, keep this empty namespace for inspection rather than hiding the failure.
    kube('create', 'namespace', NS)
    kube('label', 'namespace', NS, 'app.kubernetes.io/part-of=cnf-m1')
    save(out, 'server-dry-run.txt', kube('apply', '--dry-run=server', '-f', MANIFEST).stdout)
    save(out, 'apply.txt', kube('apply', '-f', MANIFEST, timeout=90).stdout)
    for name in ['cnf-m1-backend', 'cnf-m1-envoy']:
        save(out, name + '-rollout.txt', kube('-n', NS, 'rollout', 'status', 'deployment/' + name,
                                             '--timeout=120s', timeout=130).stdout)
    save(out, 'after-deploy-pods.json', snapshot())


def upf_observation(out, label, upf):
    data = {}
    for name, command in {
        'interfaces': ['cat', '/proc/net/dev'],
        'nat': ['iptables', '-t', 'nat', '-vnxL', 'POSTROUTING'],
        'route': ['ip', '-4', 'route'],
    }.items():
        data[name] = kube('-n', CORE, 'exec', upf, '--', *command, timeout=10).stdout
        save(out, f'upf-{label}-{name}.txt', data[name])
    return data


def path_counters(text):
    for line in text.splitlines():
        if line.strip().startswith('upfgtp:'):
            fields = line.split(':', 1)[1].split()
            return int(fields[0]), int(fields[8])
    raise RuntimeError('Missing UPF gtp5g interface counters')


def verify_image_lock(pod, expected):
    # containerStatuses.image is a runtime display name (tag or config hash).
    # Check the immutable requested reference and actual imageID separately.
    if len(pod['containers']) != 1:
        raise RuntimeError('Expected one container in each M1 Pod')
    container = pod['containers'][0]
    if container['specImage'].removeprefix('docker.io/') != expected.removeprefix('docker.io/'):
        raise RuntimeError('Live M1 image spec differs from the image lock')
    if container['imageID'].split('@')[-1] != expected.split('@')[-1]:
        raise RuntimeError('Live M1 imageID Digest differs from the image lock')


def validate_evidence(iperf, envoy_text, backend_text, envoy_ip, service_ip, before, after):
    version = json.loads(LOCK.read_text(encoding='utf-8'))['configVersion']
    if iperf.get('error') or iperf.get('end', {}).get('sum_received', {}).get('bytes', 0) <= 0:
        raise RuntimeError('iperf did not complete with received TCP payload')
    logs = []
    for line in envoy_text.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get('experiment') == version:
            logs.append(item)
    successful = [x for x in logs if x.get('cluster') == 'm1_backend' and
                  x.get('upstream') == service_ip + ':5201' and x.get('flags') == '-']
    if len(successful) < 2 or not any(int(x.get('bytes_received') or 0) > 0 for x in successful):
        raise PendingProxyLogs('Missing successful Envoy control/data connection logs')
    if not re.search(r'Accepted connection from ' + re.escape(envoy_ip) + r'[,\s]', backend_text):
        raise RuntimeError('Backend log does not show a connection from the Envoy Pod')
    first, last = path_counters(before['interfaces']), path_counters(after['interfaces'])
    if not all(b > a for a, b in zip(first, last)):
        raise RuntimeError('UPF gtp5g counters did not increase in both directions')
    return {'receivedBytes': iperf['end']['sum_received']['bytes'],
            'receiverMbps': iperf['end']['sum_received']['bits_per_second'] / 1e6,
            'senderMbps': iperf['end'].get('sum_sent', {}).get('bits_per_second', 0) / 1e6,
            'senderWindowSeconds': iperf['end'].get('sum_sent', {}).get('seconds'),
            'receiverWindowSeconds': iperf['end']['sum_received'].get('seconds'),
            'envoySuccessfulConnections': len(successful),
            'upfGtpRxBytesDelta': last[0] - first[0], 'upfGtpTxBytesDelta': last[1] - first[1],
            'pathEvidence': 'source-bound UE route, UPF interface/NAT observations, Envoy access log, backend peer log',
            'limitations': ['UPF counters are aggregate; this is not per-flow packet capture or a throughput benchmark']}


def probe(out):
    state = baseline(out, 'before-probe')
    node, target = target_node()
    ue, source = ue_path(out, state, target)
    envoy, envoy_state = one_pod(state, NS, 'cnf-m1-envoy')
    backend, backend_state = one_pod(state, NS, 'cnf-m1-backend')
    upf, _ = one_pod(state, CORE, 'free5gc-helm-free5gc-upf-upf')
    if envoy_state['node'] != node['metadata']['name'] or backend_state['node'] != node['metadata']['name']:
        raise RuntimeError('M1 Envoy and backend must both be on VM1')
    docs, lock = config()
    for component, pod in [('envoy', envoy_state), ('backend', backend_state)]:
        verify_image_lock(pod, lock['images'][component]['image'])
    expected_config = next(d['data'] for d in docs if d['kind'] == 'ConfigMap')
    actual_config = json.loads(kube('-n', NS, 'get', 'configmap', 'cnf-m1-envoy', '-o', 'json').stdout)
    if actual_config['data'] != expected_config:
        raise RuntimeError('Live Envoy configuration differs from the recorded configuration')
    busy = kube('-n', CORE, 'exec', ue, '--', 'pgrep', '-x', 'iperf3', required=False)
    if busy.returncode != 1:
        raise RuntimeError('UE already has an iperf process or process inspection failed')
    save(out, 'iperf-version.txt', kube('-n', CORE, 'exec', ue, '--', 'iperf3', '--version').stdout)
    services = json.loads(kube('-n', NS, 'get', 'services', '-o', 'json').stdout)['items']
    backend_service = next(s for s in services if s['metadata']['name'] == 'cnf-m1-backend')
    gateway = next(s for s in services if s['metadata']['name'] == 'cnf-m1-envoy')
    if gateway['spec'].get('externalTrafficPolicy') != 'Local' or gateway['spec']['ports'][0].get('nodePort') != PORT:
        raise RuntimeError('Unexpected NodePort configuration')
    if backend_service['spec']['type'] != 'ClusterIP':
        raise RuntimeError('Backend must use ClusterIP only')
    save(out, 'services.json', services)
    first = upf_observation(out, 'before', upf)
    started = now()
    # Bounded offered load is only for the M1 compatibility probe, not a tc/QoS result.
    command = ['kubectl', '-n', CORE, 'exec', ue, '--', 'iperf3', '-c', target,
               '-B', source, '-p', str(PORT), '-P', '1', '-b', '5M', '-t', '5',
               '-J', '--get-server-output', '--connect-timeout', '5000']
    save(out, 'iperf.command.json', command)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 25
    samples = []
    stdout, stderr = '', ''
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=1)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() > deadline:
                    raise RuntimeError('M1 iperf deadline exceeded')
                current = snapshot()
                save(out, 'guard-pods.json', current)
                assert_steady(state, current)
                memory = int(kube('-n', CORE, 'exec', upf, '--', 'cat',
                                  '/sys/fs/cgroup/memory.current', timeout=5).stdout)
                samples.append({'time': now(), 'upfMemoryBytes': memory})
                if memory >= 512 * 1024 ** 2:
                    raise RuntimeError('UPF reached the 512 MiB stop threshold')
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
            kube('-n', CORE, 'exec', ue, '--', 'pkill', '-TERM', '-x', 'iperf3', required=False)
        save(out, 'resource-samples.json', samples)
        save(out, 'iperf.json', stdout)
        save(out, 'iperf.stderr.txt', stderr)
    if process.returncode:
        raise RuntimeError('iperf command failed; see raw output')
    last = upf_observation(out, 'after', upf)
    assert_steady(state, snapshot())
    # The control connection may close after iperf emits its JSON. Log sinks also
    # flush asynchronously. Wait for both records without weakening the evidence.
    drain_started = time.monotonic()
    while True:
        envoy_log = kube('-n', NS, 'logs', envoy, '--since-time=' + started).stdout
        backend_log = kube('-n', NS, 'logs', backend, '--since-time=' + started).stdout
        save(out, 'envoy-access.txt', envoy_log)
        save(out, 'backend.txt', backend_log)
        try:
            evidence = validate_evidence(json.loads(stdout), envoy_log, backend_log, envoy_state['ip'],
                                         backend_service['spec']['clusterIP'], first, last)
            break
        except PendingProxyLogs:
            if time.monotonic() - drain_started >= LOG_DRAIN_SECONDS:
                raise
            assert_steady(state, snapshot())
            time.sleep(0.25)
    evidence['accessLogWaitSeconds'] = round(time.monotonic() - drain_started, 3)
    final = baseline(out, 'after-probe')
    assert_steady(state, final)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['render', 'm0', 'deploy', 'probe'])
    parser.add_argument('--out', help='New, unique result directory; never overwrite an earlier run')
    args = parser.parse_args()
    docs, lock = config()
    if args.command == 'render' and not args.out:
        print(MANIFEST.read_text(encoding='utf-8'), end='')
        return
    if not args.out:
        parser.error('--out is required for cluster commands')
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    metadata = {'route': 'R2', 'stage': 'R2.S2', 'milestone': 'M0' if args.command == 'm0' else 'M1',
                'configVersion': lock['configVersion'], 'command': args.command, 'startedAt': now(),
                'evidencePath': str(out), 'sha256LF': {str(p.relative_to(ROOT)): hashlib.sha256(
                p.read_bytes().replace(b'\r\n', b'\n')).hexdigest() for p in [MANIFEST, LOCK, Path(__file__),
                    ROOT / 'scripts/free5gc-profile.py', ROOT / 'infra/profiles/free5gc-3node-no-multus-v1.json']}}
    save(out, 'metadata.json', metadata)
    save(out, 'manifest.yaml', MANIFEST.read_text(encoding='utf-8'))
    save(out, 'images.json', lock)
    result = {'outcome': 'failed', 'command': args.command}
    try:
        if args.command == 'm0':
            state = baseline(out, 'm0')
            _, target = target_node()
            ue_path(out, state, target)
        elif args.command == 'deploy':
            deploy(out, docs, lock)
        elif args.command == 'probe':
            result['evidence'] = probe(out)
        result['outcome'] = 'prepared' if args.command == 'render' else 'passed'
    except Exception as exc:
        result['error'] = str(exc)
        raise
    finally:
        result['endedAt'] = now()
        save(out, 'summary.json', result)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError, StopIteration, subprocess.TimeoutExpired) as exc:
        print(f'CNF M1 ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
