#!/usr/bin/env python3
"""R2.S2 M4: remove shaping, validate neutral baseline, move only edge and verify paths."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / 'infra/cnfs/m4-experiment.json'
module = importlib.util.spec_from_file_location('cnf_m3', ROOT / 'scripts/cnf-m3.py')
m3 = importlib.util.module_from_spec(module)
module.loader.exec_module(m3)
m2, m1 = m3.m2, m3.m1


def render(placed):
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    routing, images, inherited = m2.render()
    if spec['inheritedRouting'] != routing['configVersion']:
        raise ValueError('Routing version differs from fixed M4 input')
    core = next(d for d in m1.config()[0] if d['kind'] == 'Deployment' and d['metadata']['name'] == 'cnf-m1-backend')
    edge = next(d for d in inherited if d['kind'] == 'Deployment' and d['metadata']['name'] == 'cnf-m2-edge')
    docs = copy.deepcopy([core, edge])
    for doc in docs:
        doc['spec']['strategy'] = {'type': 'Recreate', 'rollingUpdate': None}
    if placed:
        docs[1]['spec']['template']['spec']['nodeSelector'] = spec['edgeNodeSelector']
    return spec, routing, images, docs


def node_check(out, spec, image):
    selector = ','.join(k + '=' + v for k, v in spec['edgeNodeSelector'].items())
    nodes = json.loads(m1.kube('get', 'nodes', '-o', 'json').stdout)['items']
    # Save only scheduling, networking and runtime image information, not credentials.
    evidence = [{'name': n['metadata']['name'], 'uid': n['metadata']['uid'], 'labels': n['metadata']['labels'],
                 'spec': {k: n['spec'].get(k) for k in ['podCIDR', 'podCIDRs', 'taints', 'unschedulable']},
                 'status': {k: n['status'].get(k) for k in ['addresses', 'conditions', 'nodeInfo', 'images', 'allocatable']}}
                for n in nodes]
    m1.save(out, 'nodes.json', evidence)
    matches = [n for n in nodes if all(n['metadata']['labels'].get(k) == v for k, v in spec['edgeNodeSelector'].items())]
    if len(matches) != 1:
        raise RuntimeError('Expected exactly one edge-role node: ' + selector)
    node = matches[0]
    control, _ = m1.target_node()
    if (node['metadata']['name'] == control['metadata']['name'] or node['spec'].get('unschedulable') or
            any(t.get('effect') in ['NoSchedule', 'NoExecute'] for t in node['spec'].get('taints', [])) or
            not any(c['type'] == 'Ready' and c['status'] == 'True' for c in node['status']['conditions'])):
        raise RuntimeError('Edge node is not a distinct schedulable Ready node')
    if node['status']['nodeInfo']['architecture'] != 'amd64':
        raise RuntimeError('Expected the locked linux/amd64 image architecture')
    names = {name for img in node['status'].get('images', []) for name in img['names']}
    if image not in names:
        raise RuntimeError('Edge node lacks the locked local backend image; inspect containerd before placement')
    return node


def no_shaping(data):
    roots = [q for q in data if q.get('root')]
    if len(roots) != 1 or roots[0]['kind'] != 'noqueue' or any(q['kind'] in ['tbf', 'htb'] for q in data):
        raise RuntimeError('Expected the unshaped Pod root noqueue qdisc')


def qdisc(out, name, pod):
    text = m1.kube('-n', m1.NS, 'exec', pod, '-c', 'iperf3', '--',
                   'tc', '-j', '-s', 'qdisc', 'show', 'dev', 'eth0').stdout
    m1.save(out, name, text)
    data = json.loads(text)
    no_shaping(data)
    return data


def validate_endpoint(endpoint, slices, pod_name, pod, node):
    addresses = [(a, part['ports']) for part in endpoint.get('subsets', []) for a in part.get('addresses', [])]
    if len(addresses) != 1 or any(s.get('notReadyAddresses') for s in endpoint.get('subsets', [])):
        raise RuntimeError('Expected exactly one Ready legacy backend endpoint')
    address, ports = addresses[0]
    if (address.get('ip') != pod['ip'] or address.get('nodeName') != node or
            address.get('targetRef', {}).get('uid') != pod['uid'] or
            address.get('targetRef', {}).get('name') != pod_name or
            [(p['port'], p.get('protocol')) for p in ports] != [(5201, 'TCP')]):
        raise RuntimeError('Endpoint does not identify the expected backend Pod/node/port')
    entries = [e for s in slices for e in s.get('endpoints', [])]
    if (len(entries) != 1 or entries[0].get('addresses') != [pod['ip']] or
            entries[0].get('nodeName') != node or entries[0].get('conditions', {}).get('ready') is not True or
            entries[0].get('targetRef', {}).get('uid') != pod['uid'] or
            any([(p['port'], p.get('protocol')) for p in s.get('ports', [])] != [(5201, 'TCP')] for s in slices)):
        raise RuntimeError('EndpointSlice does not identify the expected Ready backend Pod/node/port')


def verify_live(out, state, spec, routing, images, docs, placed):
    out.mkdir()
    edge_node = node_check(out, spec, images['images']['backend']['image'])
    control, target = m1.target_node()
    envoy_name, envoy = m1.one_pod(state, m1.NS, routing['gatewayDeployment'])
    m1.verify_image_lock(envoy, images['images']['envoy']['image'])
    if envoy['node'] != control['metadata']['name']:
        raise RuntimeError('Envoy must remain on the control node')
    inherited = m2.render()[2]
    for doc in [d for d in inherited if d['kind'] in ['ConfigMap', 'Service'] or
                d['kind'] == 'Deployment' and d['metadata']['name'] == routing['gatewayDeployment']] + [
                d for d in m1.config()[0] if d['kind'] == 'Service' and d['metadata']['name'] == 'cnf-m1-backend']:
        live = json.loads(m1.kube('-n', m1.NS, 'get', doc['kind'], doc['metadata']['name'], '-o', 'json').stdout)
        field = 'data' if doc['kind'] == 'ConfigMap' else 'spec'
        m3.require_subset(doc[field], live[field])
        m1.save(out, doc['kind'].lower() + '-' + doc['metadata']['name'] + '.json', live)
    services = {s['metadata']['name']: s for s in json.loads(m1.kube('-n', m1.NS, 'get', 'services', '-o', 'json').stdout)['items']}
    policies = json.loads(m1.kube('-n', m1.NS, 'get', 'networkpolicies', '-o', 'json').stdout)
    m1.save(out, 'networkpolicies.json', policies)
    if policies['items']:
        raise RuntimeError('M4 expects no simultaneous NetworkPolicy change')
    backends = {}
    for route, doc in zip(routing['classes'], docs):
        name, pod = m1.one_pod(state, m1.NS, route['backendDeployment'])
        expected_node = edge_node['metadata']['name'] if placed and route['name'] == 'edge' else control['metadata']['name']
        if pod['node'] != expected_node:
            raise RuntimeError('Backend Pod is on the wrong physical VM/node')
        m1.verify_image_lock(pod, images['images']['backend']['image'])
        deployment = json.loads(m1.kube('-n', m1.NS, 'get', 'deployment', route['backendDeployment'], '-o', 'json').stdout)
        m3.require_subset(doc['spec'], deployment['spec'])
        actual = json.loads(m1.kube('-n', m1.NS, 'get', 'pod', name, '-o', 'json').stdout)['spec']
        m3.require_subset(doc['spec']['template']['spec'], actual)
        if actual.get('hostNetwork') or actual.get('initContainers') or len(actual['containers']) != 1:
            raise RuntimeError('Unexpected backend network namespace or additional containers')
        if actual['containers'][0]['securityContext'] != doc['spec']['template']['spec']['containers'][0]['securityContext']:
            raise RuntimeError('Unexpected backend container privileges')
        endpoint = json.loads(m1.kube('-n', m1.NS, 'get', 'endpoints', route['backendService'], '-o', 'json').stdout)
        slices = json.loads(m1.kube('-n', m1.NS, 'get', 'endpointslices', '-l',
                            'kubernetes.io/service-name=' + route['backendService'], '-o', 'json').stdout)['items']
        validate_endpoint(endpoint, slices, name, pod, expected_node)
        for file, value in [('deployment', deployment), ('endpoints', endpoint), ('endpointslices', slices)]:
            m1.save(out, route['name'] + '-' + file + '.json', value)
        qdisc(out, route['name'] + '-qdisc.json', name)
        m1.save(out, route['name'] + '-route-to-envoy.json', m1.kube('-n', m1.NS, 'exec', name, '--',
                    'ip', '-j', 'route', 'get', envoy['ip']).stdout)
        backends[route['name']] = (name, pod)
    m1.save(out, 'services.json', services)
    return target, envoy_name, envoy, backends, services


def unaffected(state, prefixes):
    state = copy.deepcopy(state)
    state[m1.NS] = {n: p for n, p in state[m1.NS].items() if not any(n.startswith(x + '-') for x in prefixes)}
    return state


def transition(out, spec, routing, images, docs, command):
    before = m1.baseline(out, 'before-deploy')
    node_check(out, spec, images['images']['backend']['image'])
    if command == 'normalize':
        folder = out / 'm3-before'
        folder.mkdir()
        old_spec, old_routing, old_images, old_docs = m3.render()
        m3.verify_live(folder, before, old_spec, old_routing, old_images, old_docs)
    else:
        verify_live(out / 'neutral-before', before, spec, routing, images, render(False)[3], False)
    manifest = out / 'apply-manifest.yaml'
    m1.save(out, manifest.name, yaml.safe_dump_all(docs if command == 'normalize' else [docs[1]], sort_keys=False))
    m1.save(out, 'server-dry-run.txt', m1.kube('apply', '--dry-run=server', '-f', manifest).stdout)
    m1.save(out, 'apply.txt', m1.kube('apply', '-f', manifest).stdout)
    routes = routing['classes'] if command == 'normalize' else [routing['classes'][1]]
    for route in routes:
        m1.save(out, route['name'] + '-rollout.txt', m1.kube('-n', m1.NS, 'rollout', 'status',
            'deployment/' + route['backendDeployment'], '--timeout=120s', timeout=130).stdout)
    after = m1.baseline(out, 'after-deploy')
    prefixes = [r['backendDeployment'] for r in routes]
    m1.assert_steady(unaffected(before, prefixes), unaffected(after, prefixes))
    verify_live(out / 'live-after', after, spec, routing, images, docs, command == 'place')


def validate_flow(data, logs, correct, wrong, route, routing, service_ip, envoy_ip, started, ended, direction, duration):
    if direction == 'downlink':
        return m3.validate_downlink(data, logs, correct, wrong, route, routing, service_ip, envoy_ip, started, ended, duration)
    if data.get('start', {}).get('test_start', {}).get('reverse') != 0:
        raise RuntimeError('Unexpected uplink test direction')
    result = m2.validate_round(data, logs, correct, wrong, route, routing, service_ip, envoy_ip, started, ended)
    result['receiverMbps'] = data['end']['sum_received']['bits_per_second'] / 1e6
    return result


def measure(out, spec, routing, images, docs, placed, smoke):
    state = m1.baseline(out, 'before-measure')
    target, envoy_name, envoy, backends, services = verify_live(out / 'live-before', state, spec, routing, images, docs, placed)
    ue, source = m1.ue_path(out, state, target)
    upf, _ = m1.one_pod(state, m1.CORE, 'free5gc-helm-free5gc-upf-upf')
    if m1.kube('-n', m1.CORE, 'exec', ue, '--', 'pgrep', '-x', 'iperf3', required=False).returncode != 1:
        raise RuntimeError('UE already runs iperf or inspection failed')
    count = 1 if smoke else spec['roundsPerClassPerDirection']
    rows = []
    try:
        for number in range(1, count + 1):
            for direction in spec['directions']:
                for route in routing['classes']:
                    folder = out / f'{number:02d}-{direction}-{route["name"]}'
                    folder.mkdir()
                    row = {'round': number, 'businessClass': route['name'], 'direction': direction,
                           'backendNode': backends[route['name']][1]['node'], 'outcome': 'failed'}
                    try:
                        m1.assert_steady(state, m1.snapshot())
                        pod = backends[route['name']][0]
                        qdisc(folder, 'qdisc-before.json', pod)
                        first = m1.upf_observation(folder, 'before', upf)
                        started = m1.now()
                        row['startedAt'] = started
                        command = ['kubectl', '-n', m1.CORE, 'exec', ue, '--', 'iperf3', '-c', target,
                            '-B', source, '-p', str(route['nodePort']), '-P', '1', '-b', str(spec['offeredMbps']) + 'M',
                            '-t', str(spec['durationSeconds']), '-l', str(spec['blockBytes']),
                            '-J', '--get-server-output', '--connect-timeout', '5000']
                        if direction == 'downlink':
                            command.append('-R')
                        m1.save(folder, 'command.json', command)
                        data = m3.transfer(folder, ue, upf, state, command, spec['durationSeconds'])
                        ended = m1.now()
                        row['trafficEndedAt'] = ended
                        qdisc(folder, 'qdisc-after.json', pod)
                        last = m1.upf_observation(folder, 'after', upf)
                        delta = [b - a for a, b in zip(m1.path_counters(first['interfaces']), m1.path_counters(last['interfaces']))]
                        if not all(x > 0 for x in delta):
                            raise RuntimeError('UPF did not record bidirectional traffic')
                        row.update(upfGtpRxBytesDelta=delta[0], upfGtpTxBytesDelta=delta[1])
                        other = next(r for r in routing['classes'] if r['name'] != route['name'])
                        waiting = time.monotonic()
                        while True:
                            logs = m1.kube('-n', m1.NS, 'logs', envoy_name, '--since-time=' + started).stdout
                            correct = m1.kube('-n', m1.NS, 'logs', pod, '--since-time=' + started).stdout
                            wrong = m1.kube('-n', m1.NS, 'logs', backends[other['name']][0], '--since-time=' + started).stdout
                            for filename, value in [('envoy-access.txt', logs), ('expected-backend.txt', correct), ('other-backend.txt', wrong)]:
                                m1.save(folder, filename, value)
                            try:
                                evidence = validate_flow(data, logs, correct, wrong, route, routing,
                                    services[route['backendService']]['spec']['clusterIP'], envoy['ip'],
                                    started, ended, direction, spec['durationSeconds'])
                                break
                            except m1.PendingProxyLogs:
                                if time.monotonic() - waiting >= 15:
                                    raise
                                m1.assert_steady(state, m1.snapshot())
                                time.sleep(.25)
                        m1.assert_steady(state, m1.snapshot())
                        row.update(evidence, outcome='passed', logWaitSeconds=round(time.monotonic() - waiting, 3))
                        print(f'{number:02d}/{count:02d} {direction} {route["name"]} @ {row["backendNode"]}: passed', flush=True)
                    except Exception as exc:
                        row['error'] = str(exc)
                        raise
                    finally:
                        rows.append(row)
                        m1.save(folder, 'summary.json', row)
                        m1.save(out, 'rounds.json', rows)
    finally:
        m1.save(out, 'final-pods.json', m1.snapshot())
    final = m1.baseline(out, 'after-measure')
    m1.assert_steady(state, final)
    verify_live(out / 'live-after', final, spec, routing, images, docs, placed)
    return {'sessions': len(rows), 'proxyConnections': sum(r['proxyConnections'] for r in rows),
            'incorrectRoutes': 0, 'fullAcceptance': placed and not smoke,
            'correctPerClassPerDirection': count, 'placement': {k: p['node'] for k, (_, p) in backends.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['render', 'normalize', 'measure-neutral', 'place', 'measure'])
    parser.add_argument('--out', required=True)
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    placed = args.command in ['render', 'place', 'measure']
    spec, routing, images, docs = render(placed)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    inputs = [SPEC, m3.SPEC, m2.SPEC, m1.MANIFEST, m1.LOCK, Path(__file__), ROOT / 'scripts/cnf-m3.py',
              ROOT / 'scripts/cnf-m2.py', ROOT / 'scripts/cnf-m1.py', ROOT / 'scripts/free5gc-profile.py',
              ROOT / 'infra/profiles/free5gc-3node-no-multus-v1.json']
    m1.save(out, 'metadata.json', {**{k: spec[k] for k in ['route', 'stage', 'milestone', 'configVersion']},
        'command': args.command, 'smoke': args.smoke, 'placed': placed, 'startedAt': m1.now(),
        'evidencePath': str(out), 'sha256LF': {str(p.relative_to(ROOT)): m2.sha(p) for p in inputs}})
    for filename, value in [('experiment.json', spec), ('routing.json', routing), ('images.json', images)]:
        m1.save(out, filename, value)
    m1.save(out, 'manifest.yaml', yaml.safe_dump_all(docs, sort_keys=False))
    result = {'outcome': 'failed', 'command': args.command}
    try:
        if args.command in ['normalize', 'place']:
            transition(out, spec, routing, images, docs, args.command)
        elif args.command in ['measure-neutral', 'measure']:
            result['evidence'] = measure(out, spec, routing, images, docs, placed,
                                         args.smoke or args.command == 'measure-neutral')
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
        print(f'CNF M4 ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
