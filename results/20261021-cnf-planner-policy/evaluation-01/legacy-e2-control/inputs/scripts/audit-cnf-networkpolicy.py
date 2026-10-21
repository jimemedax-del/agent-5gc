#!/usr/bin/env python3
"""Offline audit of NetworkPolicy ABA controls, raw connections and UE path evidence."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path

import yaml


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def require(value, reason):
    if not value:
        raise ValueError(reason)


def identity(state):
    return {(ns, name): (p['uid'], tuple((c['name'], c['containerID'], c['restartCount']) for c in p['containers']))
            for ns, pods in state.items() for name, p in pods.items()}


def date(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def audit(folder, repo):
    spec, routing = read(folder / 'experiment.json'), read(folder / 'routing.json')
    initial, state, stable, final = [read(folder / f'{label}-pods.json') for label in
                                   ['before-prepare', 'before-experiment', 'after-experiment', 'final']]
    require(identity(initial) == identity(final), 'Original 5GC/CNF identities changed')
    require(identity(state) == identity(stable), 'Identities changed across policy toggles')
    require(all(c['ready'] and c['restartCount'] == 0 for pods in final.values()
                for p in pods.values() for c in p['containers']), 'Final readiness/restart failure')
    require(read(folder / 'runtime-before.json') == read(folder / 'runtime-after.json'), 'Runtime configuration changed')
    runtime = read(folder / 'runtime-before.json')
    policies = list(yaml.safe_load_all((folder / 'policies.yaml').read_text(encoding='utf-8')))
    require(policies == list(yaml.safe_load_all((repo / 'infra/cnfs/networkpolicy.yaml').read_text(encoding='utf-8'))),
            'Saved policy differs from source')
    for label in ['policies-before', 'policies-removed']:
        require(read(folder / (label + '.json')) == [], 'Open control still has policies')
    for label in ['policies-initial', 'policies-final', 'policies-after-cleanup']:
        actual = read(folder / (label + '.json'))
        require({p['metadata']['name']: p['spec'] for p in actual} ==
                {p['metadata']['name']: p['spec'] for p in policies}, 'Policy set/spec differs')
    require(read(folder / 'policies-preserve.json')['returnCode'] == 0, 'Final policy preservation failed')
    envoy_name, envoy = next((n, p) for n, p in initial['cnf-lab'].items() if n.startswith(routing['gatewayDeployment'] + '-'))
    backends = {r['name']: next(p for n, p in initial['cnf-lab'].items() if n.startswith(r['backendDeployment'] + '-'))
                for r in routing['classes']}
    targets = {ip for r in routing['classes'] for ip in
        [runtime['services'][r['backendService']]['clusterIP'], backends[r['name']]['ip']]}
    probes = {p['name']: state['cnf-lab'][p['name']] for p in spec['probes']}
    require(set(initial['cnf-lab']) == set(final['cnf-lab']) and not set(probes) & set(final['cnf-lab']), 'Probe cleanup incomplete')
    require(probes[spec['probes'][0]['name']]['node'] == backends['core']['node'] and
            probes[spec['probes'][1]['name']]['node'] == backends['edge']['node'], 'Probe node placement differs')
    probe_ips = {p['ip'] for p in probes.values()}
    all_keys, stage_counts = None, {}
    for stage, restricted, count in [('open-before', False, 1), ('isolated-initial', True, 1),
                                      ('open-after-removal', False, 1), ('isolated-final', True, spec['isolationRounds'])]:
        raw_rows = []
        for number in range(1, count + 1):
            rows = [read(p) for p in (folder / stage / f'{number:02d}').glob('*.json')]
            tcp = [r for r in rows if 'sourcePod' in r]
            keys = {(r['sourcePod'], r['target'], r['port']) for r in tcp}
            require(len(tcp) == len(keys) == 15, 'Missing/duplicate TCP matrix cells')
            if all_keys is None:
                all_keys = keys
            require(keys == all_keys, 'ABA controls do not use identical origins/destinations')
            for row in tcp:
                src, dst, port = row['sourcePod'], row['target'], row['port']
                require(src in {envoy_name, *probes}, 'Unexpected source')
                require((dst in targets | probe_ips and port == 5201) or src == envoy_name and port == 53,
                        'Unexpected probe destination')
                allowed = not restricted or src == envoy_name and (dst in targets or port == 53)
                require(row['expectedAllowed'] == allowed, 'Incorrect expected isolation outcome')
                code = row['returnCode']
                require(code == 0 if allowed else code == 124 or code == 1 and any(t in row['stderr'] for t in
                    ['Connection refused', 'Connection timed out', 'No route to host', 'Network is unreachable']),
                    'Raw connection does not prove expected permission/denial')
                raw_rows.append(row)
            for route in routing['classes']:
                dns = read(folder / stage / f'{number:02d}' / ('dns-' + route['name'] + '.json'))
                require(dns['returnCode'] == 0 and {s.split()[0] for s in dns['stdout'].splitlines()} ==
                        {runtime['services'][route['backendService']]['clusterIP']}, 'DNS failed/wrong destination')
        aggregate = read(folder / stage / 'connections.json')
        require(len(aggregate) == len(raw_rows) and all(r['outcome'] == 'passed' for r in aggregate), 'Matrix summary mismatch')
        by_time = {r['at']: r for r in aggregate}
        require(all(all(by_time[r['at']][k] == v for k, v in r.items()) for r in raw_rows), 'Raw/summary matrix differs')
        stage_counts[stage] = {'allowed': sum(r['expectedAllowed'] for r in raw_rows),
                               'denied': sum(not r['expectedAllowed'] for r in raw_rows)}
    business = folder / 'business'
    rounds = read(business / 'rounds.json')
    require(len(rounds) == 4 * spec['businessRoundsPerClassPerDirection'], 'Incomplete UE batch')
    cookies, samples, counts, payload = set(), [], {}, 0
    for row in rounds:
        name, direction = row['businessClass'], row['direction']
        key = name + '/' + direction
        counts[key] = counts.get(key, 0) + 1
        run = business / f'{row["round"]:02d}-{direction}-{name}'
        data = read(run / 'iperf.json')
        received = data['end']['sum_received']['bytes']
        require(read(run / 'summary.json') == row and row['outcome'] == 'passed' and not data.get('error') and
                received > 0 and data['start']['test_start']['reverse'] == int(direction == 'downlink'), 'Invalid UE transfer')
        cookie = data['start']['cookie']
        require(cookie not in cookies and cookie in (run / 'expected-backend.txt').read_text(encoding='utf-8') and
                cookie not in (run / 'other-backend.txt').read_text(encoding='utf-8'), 'Cookie route/uniqueness failure')
        cookies.add(cookie)
        require('Accepted connection from ' + envoy['ip'] + ',' in (run / 'expected-backend.txt').read_text(encoding='utf-8'), 'Backend peer differs')
        records = []
        for line in (run / 'envoy-access.txt').read_text(encoding='utf-8').splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if (isinstance(item, dict) and item.get('experiment') == routing['configVersion'] and
                    date(row['startedAt']) <= date(item['start']) <= date(row['trafficEndedAt'])):
                records.append(item)
        route = next(r for r in routing['classes'] if r['name'] == name)
        field = 'bytes_sent' if direction == 'downlink' else 'bytes_received'
        require(len(records) == 2 and all(r['flags'] == '-' and r['business_class'] == name and
                r['cluster'] == route['cluster'] and r['listener'] == route['listener'] and
                r['upstream'] == runtime['services'][route['backendService']]['clusterIP'] + ':5201' for r in records) and
                max(int(r[field]) for r in records) >= received, 'Wrong/failed proxy path')
        require(identity(read(run / 'guard-pods.json')) == identity(state), 'Guard identity changed')
        memory = read(run / 'resource-samples.json')
        require(memory and all(s['upfMemoryBytes'] < 512 * 1024 ** 2 for s in memory), 'Memory guard missing/unsafe')
        samples.extend(memory)
        counters = []
        for label in ['before', 'after']:
            line = next(s for s in (run / f'upf-{label}-interfaces.txt').read_text().splitlines() if s.strip().startswith('upfgtp:'))
            values = line.split(':')[1].split()
            counters.append((int(values[0]), int(values[8])))
        require(all(b > a for a, b in zip(*counters)), 'UPF counters did not grow bidirectionally')
        payload += received
    require(set(counts) == {c + '/' + d for c in backends for d in ['uplink', 'downlink']} and
            all(n == spec['businessRoundsPerClassPerDirection'] for n in counts.values()), 'Wrong UE class/direction count')
    hashes = read(folder / 'metadata.json')['sha256LF']
    for relative, expected in hashes.items():
        actual = hashlib.sha256((repo / relative).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
        require(actual == expected, 'Source hash mismatch: ' + relative)
    require(read(folder / 'summary.json')['outcome'] == 'passed', 'Experiment did not complete')
    return {'route': 'R2', 'stage': 'R2.S2', 'milestone': 'NetworkPolicy', 'configVersion': spec['configVersion'],
            'outcome': 'passed', 'matrix': stage_counts, 'ueSessions': counts, 'uniqueCookies': len(cookies),
            'proxyConnections': 2 * len(rounds), 'receivedBytes': payload, 'original5gcPodsUnchanged': len(initial['free5gc']),
            'allOriginalPodsUnchanged': sum(len(p) for p in initial.values()), 'finalContainerRestarts': 0,
            'sourceHashChecks': len(hashes), 'upfMemorySamples': len(samples),
            'sampledPeakUpfMiB': max(s['upfMemoryBytes'] for s in samples) / 1024 ** 2,
            'probePodsRemoved': True, 'finalPolicies': [p['metadata']['name'] for p in policies]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path, help='One completed run directory')
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.results, Path(__file__).resolve().parent.parent)
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
