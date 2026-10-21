#!/usr/bin/env python3
"""Offline M4 audit from raw placement, endpoint, traffic and identity evidence."""
import argparse
from datetime import datetime
import hashlib
import ipaddress
import json
from pathlib import Path

import yaml


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identity(state):
    return {(ns, n): (p['uid'], tuple((c['name'], c['containerID'], c['restartCount']) for c in p['containers']))
            for ns, pods in state.items() for n, p in pods.items()}


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def audit(folder, batch, repo):
    accepted = folder / batch
    spec, routing = read(accepted / 'experiment.json'), read(accepted / 'routing.json')
    before, after = [read(accepted / (x + '-measure-pods.json')) for x in ['before', 'after']]
    require(identity(before) == identity(after), 'Batch identity changed')
    first = read(folder / 'normalize-01/before-deploy-pods.json')
    require(identity({'free5gc': first['free5gc']}) == identity({'free5gc': after['free5gc']}), 'Original 5GC identity changed')
    envoy_name, envoy = next((n, p) for n, p in before['cnf-lab'].items() if n.startswith(routing['gatewayDeployment'] + '-'))
    require(identity({'cnf-lab': {envoy_name: first['cnf-lab'][envoy_name]}}) ==
            identity({'cnf-lab': {envoy_name: envoy}}), 'Envoy was replaced/restarted')
    for stage in ['normalize-01', 'neutral-01', 'place-01', 'smoke-01']:
        require(read(folder / stage / 'summary.json')['outcome'] == 'passed', 'A prerequisite stage failed')
    neutral = read(folder / 'neutral-01/after-measure-pods.json')
    core_name = next(n for n in neutral['cnf-lab'] if n.startswith('cnf-m1-backend-'))
    require(identity({'cnf-lab': {core_name: neutral['cnf-lab'][core_name]}}) ==
            identity({'cnf-lab': {core_name: after['cnf-lab'][core_name]}}), 'Core changed during edge placement')
    require(all(c['ready'] and c['restartCount'] == 0 for pods in after.values()
                for p in pods.values() for c in p['containers']), 'Final readiness/restart failure')
    original_manifest = list(yaml.safe_load_all((folder / 'normalize-01/manifest.yaml').read_text(encoding='utf-8')))
    placed_manifest = list(yaml.safe_load_all((folder / 'place-01/manifest.yaml').read_text(encoding='utf-8')))
    original_manifest[1]['spec']['template']['spec']['nodeSelector'] = spec['edgeNodeSelector']
    require(original_manifest == placed_manifest, 'Placement changed more than the edge node selector')
    applied = list(yaml.safe_load_all((folder / 'place-01/apply-manifest.yaml').read_text(encoding='utf-8')))
    require(applied == [placed_manifest[1]], 'Placement applied resources beyond the edge Deployment')
    nodes = read(accepted / 'live-before/nodes.json')
    edge_node = next(n['name'] for n in nodes if all(n['labels'].get(k) == v for k, v in spec['edgeNodeSelector'].items()))
    require(envoy['node'] != edge_node, 'Edge and Envoy are on the same node')
    services = read(accepted / 'live-before/services.json')
    pods = {}
    for route in routing['classes']:
        name = route['name']
        pod_name, pod = next((n, p) for n, p in before['cnf-lab'].items() if n.startswith(route['backendDeployment'] + '-'))
        target = edge_node if name == 'edge' else envoy['node']
        require(pod['node'] == target, 'Backend is on the wrong node')
        target_info = next(n for n in nodes if n['name'] == target)
        require(ipaddress.ip_address(pod['ip']) in ipaddress.ip_network(target_info['spec']['podCIDR']),
                'Pod address is outside the actual node PodCIDR')
        pods[name] = pod
        neutral_pod = next(p for n, p in neutral['cnf-lab'].items() if n.startswith(route['backendDeployment'] + '-'))
        require(neutral_pod['node'] == envoy['node'], 'Neutral baseline already had cross-node placement')
        for checkpoint in ['live-before', 'live-after']:
            live = accepted / checkpoint
            deployment = read(live / (name + '-deployment.json'))
            neutral_template = read(folder / 'neutral-01/live-before' / (name + '-deployment.json'))['spec']['template']
            if name == 'edge':
                neutral_template['spec']['nodeSelector'] = spec['edgeNodeSelector']
            require(deployment['spec']['template'] == neutral_template, 'Live backend settings changed beyond placement')
            containers = deployment['spec']['template']['spec']['containers']
            require(len(containers) == 1 and containers[0]['name'] == 'iperf3' and
                    containers[0]['securityContext']['capabilities'] == {'drop': ['ALL']}, 'Shaping helper or privileges remain')
            expected_image = read(accepted / 'images.json')['images']['backend']['image']
            require(containers[0]['image'] == expected_image and len(pod['containers']) == 1 and
                    pod['containers'][0]['specImage'] == expected_image and
                    pod['containers'][0]['imageID'].split('@')[-1] == expected_image.split('@')[-1], 'Backend image drift')
            endpoint = read(live / (name + '-endpoints.json'))
            addresses = [a for s in endpoint.get('subsets', []) for a in s.get('addresses', [])]
            require(len(addresses) == 1 and addresses[0]['ip'] == pod['ip'] and addresses[0]['nodeName'] == target and
                    addresses[0]['targetRef']['uid'] == pod['uid'], 'Endpoint does not identify current target Pod')
            slices = read(live / (name + '-endpointslices.json'))
            entries = [e for s in slices for e in s.get('endpoints', [])]
            require(len(entries) == 1 and entries[0]['conditions']['ready'] and entries[0]['nodeName'] == target and
                    entries[0]['addresses'] == [pod['ip']] and entries[0]['targetRef']['uid'] == pod['uid'], 'Wrong/unready EndpointSlice')
            require(read(live / 'networkpolicies.json')['items'] == [], 'Concurrent policy change')
            peer_route = read(live / (name + '-route-to-envoy.json'))
            require(len(peer_route) == 1 and peer_route[0]['dst'] == envoy['ip'] and
                    peer_route[0]['prefsrc'] == pod['ip'] and peer_route[0]['dev'] == 'eth0', 'Unexpected backend return route')
    cookies, connections, total, samples, counts = set(), 0, 0, [], {}
    rows = read(accepted / 'rounds.json')
    require(len(rows) == 2 * len(spec['directions']) * spec['roundsPerClassPerDirection'], 'Incomplete formal batch')
    for row in rows:
        name, direction = row['businessClass'], row['direction']
        counts[name + '/' + direction] = counts.get(name + '/' + direction, 0) + 1
        run = accepted / f'{row["round"]:02d}-{direction}-{name}'
        require(read(run / 'summary.json') == row and row['outcome'] == 'passed', 'Round summary mismatch/failure')
        raw = read(run / 'iperf.json')
        command = read(run / 'command.json')
        require(not raw.get('error') and raw['start']['test_start']['reverse'] == int(direction == 'downlink') and
                ('-R' in command) == (direction == 'downlink') and command[command.index('-b') + 1] == '1M' and
                command[command.index('-t') + 1] == str(spec['durationSeconds']) and
                raw['start']['test_start']['duration'] == spec['durationSeconds'] and
                raw['end']['sum_sent']['seconds'] >= .95 * spec['durationSeconds'], 'Wrong/incomplete workload')
        payload = raw['end']['sum_received']['bytes']
        require(payload > 0 and payload <= raw['end']['sum_sent']['bytes'], 'Invalid received payload')
        cookie = raw['start']['cookie']
        require(cookie not in cookies, 'Duplicate cookie')
        cookies.add(cookie)
        total += payload
        for qfile in ['qdisc-before.json', 'qdisc-after.json']:
            queues = read(run / qfile)
            require([q['kind'] for q in queues if q.get('root')] == ['noqueue'] and
                    not any(q['kind'] in ['tbf', 'htb'] for q in queues), 'Shaping remains active')
        correct = (run / 'expected-backend.txt').read_text(encoding='utf-8')
        wrong = (run / 'other-backend.txt').read_text(encoding='utf-8')
        require(cookie in correct and cookie not in wrong and 'Accepted connection from ' + envoy['ip'] + ',' in correct,
                'Backend peer/cookie mismatch')
        route = next(r for r in routing['classes'] if r['name'] == name)
        records = []
        for line in (run / 'envoy-access.txt').read_text(encoding='utf-8').splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if (isinstance(entry, dict) and entry.get('experiment') == routing['configVersion'] and
                    timestamp(row['startedAt']) <= timestamp(entry['start']) <= timestamp(row['trafficEndedAt'])):
                records.append(entry)
        byte_field = 'bytes_sent' if direction == 'downlink' else 'bytes_received'
        require(len(records) == 2 and all(r['flags'] == '-' and r['business_class'] == name and
                r['cluster'] == route['cluster'] and r['listener'] == route['listener'] and
                r['upstream'] == services[route['backendService']]['spec']['clusterIP'] + ':5201' for r in records) and
                max(int(r[byte_field]) for r in records) >= payload, 'Wrong proxy path/direction/payload')
        connections += len(records)
        require(identity(read(run / 'guard-pods.json')) == identity(before), 'Guard identity drift')
        measurements = read(run / 'resource-samples.json')
        require(measurements and all(0 < s['upfMemoryBytes'] < 512 * 1024 ** 2 for s in measurements), 'Missing/unsafe memory samples')
        samples.extend(measurements)
        counters = []
        for label in ['before', 'after']:
            line = next(s for s in (run / f'upf-{label}-interfaces.txt').read_text().splitlines() if s.strip().startswith('upfgtp:'))
            values = line.split(':')[1].split()
            counters.append((int(values[0]), int(values[8])))
        require(all(y > x for x, y in zip(*counters)), 'No bidirectional UPF counter growth')
    require(set(counts) == {n + '/' + d for n in pods for d in spec['directions']} and
            all(n == spec['roundsPerClassPerDirection'] for n in counts.values()), 'Wrong class/direction count')
    hashes = 0
    for metadata in folder.glob('*/metadata.json'):
        for relative, expected in read(metadata)['sha256LF'].items():
            actual = hashlib.sha256((repo / relative).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            require(actual == expected, 'Input source hash differs: ' + relative)
            hashes += 1
    summary = read(accepted / 'summary.json')
    require(summary['outcome'] == 'passed' and summary['evidence']['fullAcceptance'], 'No full acceptance')
    return {'route': spec['route'], 'stage': spec['stage'], 'milestone': 'M4', 'configVersion': spec['configVersion'],
            'outcome': 'passed', 'acceptedBatch': batch, 'correctSessions': counts, 'uniqueCookies': len(cookies),
            'proxyConnections': connections, 'receivedBytes': total, 'backendNodes': {k: p['node'] for k, p in pods.items()},
            'backendIPs': {k: p['ip'] for k, p in pods.items()}, 'envoyNode': envoy['node'],
            'original5gcPodsUnchanged': len(first['free5gc']), 'finalContainerRestarts': 0,
            'upfMemorySamples': len(samples), 'sampledPeakUpfMiB': max(s['upfMemoryBytes'] for s in samples) / 1024 ** 2,
            'sourceHashChecks': hashes, 'limitations': spec['limitations']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--batch', default='measure-01')
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.results, args.batch, Path(__file__).resolve().parent.parent)
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
