#!/usr/bin/env python3
"""Fixed R2.S2 NetworkPolicy ABA controls, same/cross-node probes and UE regression."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / 'infra/cnfs/networkpolicy-experiment.json'
POLICY = ROOT / 'infra/cnfs/networkpolicy.yaml'
module = importlib.util.spec_from_file_location('cnf_m4', ROOT / 'scripts/cnf-m4.py')
m4 = importlib.util.module_from_spec(module)
module.loader.exec_module(m4)
m3, m2, m1 = m4.m3, m4.m2, m4.m1


def render():
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    placement, routing, images, backends = m4.render(True)
    if spec['inheritedPlacement'] != placement['configVersion']:
        raise ValueError('Unexpected inherited placement version')
    policies = list(yaml.safe_load_all(POLICY.read_text(encoding='utf-8')))
    probes = []
    for item in spec['probes']:
        pod = copy.deepcopy(backends[0]['spec']['template']['spec'])
        pod['nodeSelector'] = item['nodeSelector']
        pod['restartPolicy'] = 'Never'
        probes.append({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': item['name'], 'namespace': m1.NS,
            'labels': {'app': item['name'], 'app.kubernetes.io/part-of': 'cnf-netpol'}}, 'spec': pod})
    return spec, placement, routing, images, backends, policies, probes


def policy_state(out, label, policies, expected):
    items = json.loads(m1.kube('-n', m1.NS, 'get', 'networkpolicies', '-o', 'json').stdout)['items']
    m1.save(out, label + '.json', items)
    if not expected:
        if items:
            raise RuntimeError('Expected no cnf-lab policies')
        return
    if {p['metadata']['name'] for p in items} != {p['metadata']['name'] for p in policies}:
        raise RuntimeError('Unexpected policy set; additive allow policies would invalidate isolation')
    for item in items:
        wanted = next(p for p in policies if p['metadata']['name'] == item['metadata']['name'])
        if item['spec'] != wanted['spec']:
            raise RuntimeError('Live NetworkPolicy differs from fixed policy')


def runtime():
    result = {}
    for kind in ['deployments', 'services', 'configmaps']:
        result[kind] = {d['metadata']['name']: d.get('spec', d.get('data')) for d in
            json.loads(m1.kube('-n', m1.NS, 'get', kind, '-o', 'json').stdout)['items']}
    return result


def steady(state, locked):
    m1.assert_steady(state, m1.snapshot())
    if runtime() != locked:
        raise RuntimeError('Existing CNF runtime configuration changed')


def validate_connection(code, stderr, allowed):
    if allowed and code != 0:
        raise RuntimeError('Expected allowed TCP connection failed')
    if not allowed and (code not in [1, 124] or code == 1 and not any(s in stderr for s in
            ['Connection refused', 'Connection timed out', 'No route to host', 'Network is unreachable'])):
        raise RuntimeError('Failure is not an expected TCP rejection/timeout; cannot count tool errors as denial')


def tcp(out, label, pod, host, port, allowed, spec):
    command = ['kubectl', '-n', m1.NS, 'exec', pod, '--', 'timeout', str(spec['connectTimeoutSeconds']),
               'bash', '-c', 'exec 3<>/dev/tcp/"$1"/"$2"; exec 3>&-', 'probe', host, str(port)]
    # errexit ensures a failed redirection cannot be hidden by the final close.
    command[command.index('-c') + 1] = 'set -e; ' + command[command.index('-c') + 1]
    result = m1.run(command, timeout=spec['connectTimeoutSeconds'] + 8, required=False)
    row = {'sourcePod': pod, 'target': host, 'port': port, 'expectedAllowed': allowed, 'returnCode': result.returncode,
           'stdout': result.stdout, 'stderr': result.stderr, 'command': command, 'at': m1.now()}
    m1.save(out, label + '.json', row)
    validate_connection(result.returncode, result.stderr, allowed)
    row['outcome'] = 'passed'
    return row


def matrix(out, spec, state, locked, envoy, backends, services, restricted, count):
    out.mkdir()
    results = []
    probe_names = [p['name'] for p in spec['probes']]
    dns_service = json.loads(m1.kube('-n', 'kube-system', 'get', 'service', 'kube-dns', '-o', 'json').stdout)
    for number in range(1, count + 1):
        folder = out / f'{number:02d}'
        folder.mkdir()
        steady(state, locked)
        for name, (backend, pod) in backends.items():
            route = next(r for r in m2.render()[0]['classes'] if r['name'] == name)
            service_ip = services[route['backendService']]['spec']['clusterIP']
            for target_type, host in [('service', service_ip), ('pod', pod['ip'])]:
                for source in [envoy, *probe_names]:
                    allowed = not restricted or source == envoy
                    row = tcp(folder, name + '-' + target_type + '-' + source, source, host, 5201, allowed, spec)
                    row.update(businessClass=name, targetType=target_type)
                    results.append(row)
        for probe in probe_names:
            row = tcp(folder, 'envoy-to-' + probe, envoy, state[m1.NS][probe]['ip'], 5201, not restricted, spec)
            row.update(targetType='probe', businessClass='diagnostic')
            results.append(row)
        # getent executes in the actual Envoy network namespace and bypasses its DNS cluster cache.
        for route in m2.render()[0]['classes']:
            fqdn = route['backendService'] + '.cnf-lab.svc.cluster.local.'
            command = ['kubectl', '-n', m1.NS, 'exec', envoy, '--', 'getent', 'ahostsv4', fqdn]
            response = m1.run(command, timeout=10, required=False)
            m1.save(folder, 'dns-' + route['name'] + '.json', {'command': command, 'returnCode': response.returncode,
                'stdout': response.stdout, 'stderr': response.stderr})
            if response.returncode or {line.split()[0] for line in response.stdout.splitlines()} != {
                    services[route['backendService']]['spec']['clusterIP']}:
                raise RuntimeError('Envoy DNS did not resolve the expected backend Service')
        results.append(tcp(folder, 'dns-tcp', envoy, dns_service['spec']['clusterIP'], 53, True, spec))
        steady(state, locked)
        m1.save(out, 'connections.json', results)
        print(f'{out.name} {number}/{count}: TCP allow/deny and Envoy DNS passed', flush=True)
    return results


def business(out, spec, state, locked, target, envoy_name, envoy, backends, services, routing):
    out.mkdir()
    ue, source = m1.ue_path(out, state, target)
    upf, _ = m1.one_pod(state, m1.CORE, 'free5gc-helm-free5gc-upf-upf')
    if m1.kube('-n', m1.CORE, 'exec', ue, '--', 'pgrep', '-x', 'iperf3', required=False).returncode != 1:
        raise RuntimeError('UE already runs iperf or inspection failed')
    rows = []
    for number in range(1, spec['businessRoundsPerClassPerDirection'] + 1):
        for direction in ['uplink', 'downlink']:
            for route in routing['classes']:
                folder = out / f'{number:02d}-{direction}-{route["name"]}'
                folder.mkdir()
                row = {'round': number, 'businessClass': route['name'], 'direction': direction, 'outcome': 'failed'}
                try:
                    steady(state, locked)
                    first = m1.upf_observation(folder, 'before', upf)
                    started = m1.now()
                    row['startedAt'] = started
                    command = ['kubectl', '-n', m1.CORE, 'exec', ue, '--', 'iperf3', '-c', target,
                        '-B', source, '-p', str(route['nodePort']), '-P', '1', '-b', str(spec['offeredMbps']) + 'M',
                        '-t', str(spec['durationSeconds']), '-l', '16384', '-J', '--get-server-output', '--connect-timeout', '5000']
                    if direction == 'downlink':
                        command.append('-R')
                    m1.save(folder, 'command.json', command)
                    data = m3.transfer(folder, ue, upf, state, command, spec['durationSeconds'])
                    ended = m1.now()
                    row['trafficEndedAt'] = ended
                    last = m1.upf_observation(folder, 'after', upf)
                    if not all(b > a for a, b in zip(m1.path_counters(first['interfaces']), m1.path_counters(last['interfaces']))):
                        raise RuntimeError('No bidirectional UPF counter growth')
                    other = next(r for r in routing['classes'] if r['name'] != route['name'])
                    waiting = time.monotonic()
                    while True:
                        logs = m1.kube('-n', m1.NS, 'logs', envoy_name, '--since-time=' + started).stdout
                        correct = m1.kube('-n', m1.NS, 'logs', backends[route['name']][0], '--since-time=' + started).stdout
                        wrong = m1.kube('-n', m1.NS, 'logs', backends[other['name']][0], '--since-time=' + started).stdout
                        for name, value in [('envoy-access.txt', logs), ('expected-backend.txt', correct), ('other-backend.txt', wrong)]:
                            m1.save(folder, name, value)
                        try:
                            evidence = m4.validate_flow(data, logs, correct, wrong, route, routing,
                                services[route['backendService']]['spec']['clusterIP'], envoy['ip'], started, ended,
                                direction, spec['durationSeconds'])
                            break
                        except m1.PendingProxyLogs:
                            if time.monotonic() - waiting >= 15:
                                raise
                            m1.assert_steady(state, m1.snapshot())
                            time.sleep(.25)
                    steady(state, locked)
                    row.update(evidence, outcome='passed')
                    print(f'UE {number}/5 {direction} {route["name"]}: passed', flush=True)
                except Exception as exc:
                    row['error'] = str(exc)
                    raise
                finally:
                    rows.append(row)
                    m1.save(folder, 'summary.json', row)
                    m1.save(out, 'rounds.json', rows)
    return rows


def run_experiment(out, engine_dir, data):
    spec, placement, routing, images, docs, policies, probes = data
    for node in ['vm1', 'vm3']:
        path = engine_dir / ('engine-before-' + node + '.txt')
        text = path.read_text(encoding='utf-8')
        if not all(s in text for s in ['KUBE-ROUTER-FORWARD', 'kube-router netpol']):
            raise RuntimeError('Missing observed kube-router netpol engine chains on ' + node)
        shutil.copyfile(path, out / path.name)
    initial = m1.baseline(out, 'before-prepare')
    target, envoy_name, envoy, backends, services = m4.verify_live(out / 'm4-topology', initial,
        placement, routing, images, docs, True)
    for probe in probes:
        if m1.kube('-n', m1.NS, 'get', 'pod', probe['metadata']['name'], '--ignore-not-found', '-o', 'name').stdout.strip():
            raise RuntimeError('Diagnostic Pod name already exists')
    m1.save(out, 'probes-dry-run.txt', m1.kube('apply', '--dry-run=server', '-f', out / 'probes.yaml').stdout)
    m1.save(out, 'probes-apply.txt', m1.kube('apply', '-f', out / 'probes.yaml').stdout)
    for probe in probes:
        m1.kube('-n', m1.NS, 'wait', 'pod/' + probe['metadata']['name'], '--for=condition=Ready', '--timeout=90s', timeout=100)
    state = m1.baseline(out, 'before-experiment')
    names = [p['metadata']['name'] for p in probes]
    stripped = copy.deepcopy(state)
    for name in names:
        m1.verify_image_lock(state[m1.NS][name], images['images']['backend']['image'])
        stripped[m1.NS].pop(name)
    m1.assert_steady(initial, stripped)
    locked = runtime()
    m1.save(out, 'runtime-before.json', locked)
    touched = False
    try:
        matrix(out / 'open-before', spec, state, locked, envoy_name, backends, services, False, 1)
        policy_state(out, 'policies-before', policies, False)
        m1.save(out, 'policies-dry-run.txt', m1.kube('apply', '--dry-run=server', '-f', POLICY).stdout)
        touched = True
        m1.save(out, 'policies-apply.txt', m1.kube('apply', '-f', POLICY).stdout)
        time.sleep(spec['policySettleSeconds'])
        policy_state(out, 'policies-initial', policies, True)
        matrix(out / 'isolated-initial', spec, state, locked, envoy_name, backends, services, True, 1)
        m1.save(out, 'policies-remove.txt', m1.kube('delete', '-f', POLICY).stdout)
        time.sleep(spec['policySettleSeconds'])
        policy_state(out, 'policies-removed', policies, False)
        matrix(out / 'open-after-removal', spec, state, locked, envoy_name, backends, services, False, 1)
        m1.save(out, 'policies-reapply.txt', m1.kube('apply', '-f', POLICY).stdout)
        time.sleep(spec['policySettleSeconds'])
        policy_state(out, 'policies-final', policies, True)
        connections = matrix(out / 'isolated-final', spec, state, locked, envoy_name, backends, services, True, spec['isolationRounds'])
        rows = business(out / 'business', spec, state, locked, target, envoy_name, envoy, backends, services, routing)
        stable = m1.baseline(out, 'after-experiment')
        m1.assert_steady(state, stable)
        m1.save(out, 'runtime-after.json', runtime())
        steady(state, locked)
    finally:
        if touched:
            # Keep the intended protection after failures as well; record rather than hide recovery errors.
            result = m1.kube('apply', '-f', POLICY, required=False)
            m1.save(out, 'policies-preserve.json', {'returnCode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
            if result.returncode:
                raise RuntimeError('Failed to preserve the intended final NetworkPolicy; inspect raw output')
    m1.save(out, 'probes-delete.txt', m1.kube('delete', '-f', out / 'probes.yaml', '--wait=true', timeout=60).stdout)
    final = m1.baseline(out, 'final')
    m1.assert_steady(initial, final)
    if runtime() != locked:
        raise RuntimeError('Runtime changed during cleanup')
    policy_state(out, 'policies-after-cleanup', policies, True)
    return {'businessSessions': len(rows), 'proxyConnections': sum(r['proxyConnections'] for r in rows),
        'incorrectRoutes': 0, 'isolationChecks': len(connections),
        'allowedChecks': sum(r['expectedAllowed'] for r in connections),
        'deniedChecks': sum(not r['expectedAllowed'] for r in connections),
        'probePodsRemoved': True, 'fullAcceptance': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['render', 'run'])
    parser.add_argument('--out', required=True)
    parser.add_argument('--engine-dir', type=Path)
    args = parser.parse_args()
    if args.command == 'run' and not args.engine_dir:
        parser.error('run requires --engine-dir with saved VM1/VM3 iptables engine evidence')
    data = render()
    spec, _, routing, images, _, policies, probes = data
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    inputs = [SPEC, POLICY, m4.SPEC, m3.SPEC, m2.SPEC, m1.MANIFEST, m1.LOCK, Path(__file__),
        *[ROOT / ('scripts/cnf-m' + str(n) + '.py') for n in range(1, 5)], ROOT / 'scripts/free5gc-profile.py',
        ROOT / 'infra/profiles/free5gc-3node-no-multus-v1.json']
    m1.save(out, 'metadata.json', {**{k: spec[k] for k in ['route', 'stage', 'milestone', 'configVersion']},
        'command': args.command, 'startedAt': m1.now(), 'evidencePath': str(out),
        'sha256LF': {str(p.relative_to(ROOT)): m2.sha(p) for p in inputs}})
    for name, value in [('experiment.json', spec), ('routing.json', routing), ('images.json', images)]:
        m1.save(out, name, value)
    m1.save(out, 'policies.yaml', yaml.safe_dump_all(policies, sort_keys=False))
    m1.save(out, 'probes.yaml', yaml.safe_dump_all(probes, sort_keys=False))
    result = {'outcome': 'failed', 'command': args.command}
    try:
        if args.command == 'run':
            result['evidence'] = run_experiment(out, args.engine_dir, data)
        result['outcome'] = 'prepared' if args.command == 'render' else 'passed'
    except Exception as exc:
        result['error'] = str(exc)
        raise
    finally:
        result['endedAt'] = m1.now()
        m1.save(out, 'summary.json', result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, KeyError, StopIteration, subprocess.TimeoutExpired) as exc:
        print(f'CNF NetworkPolicy ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
