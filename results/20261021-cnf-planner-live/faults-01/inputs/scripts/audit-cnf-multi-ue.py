#!/usr/bin/env python3
"""Offline audit of the fixed two-UE A batch, reconstructed from raw evidence."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def require(value, message):
    if not value:
        raise ValueError(message)


def ids(state):
    return {(ns, name): (p['uid'], tuple((c['name'], c['containerID'], c['restartCount']) for c in p['containers']))
            for ns, pods in state.items() for name, p in pods.items()}


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def window(data):
    active = [x['sum'] for x in data['intervals'] if x['sum']['bytes'] > 0]
    require(active and sum(x['end'] - x['start'] for x in active) >= 9,
            'Insufficient positive-byte interval coverage')
    require(all(b['start'] - a['end'] <= .1 for a, b in zip(active, active[1:])), 'Gaps in active traffic intervals')
    t = data['start']['timestamp']['timesecs']
    return t + active[0]['start'] + 1, t + active[-1]['end']


def raw_flow(folder, row, route, topology, spec):
    data = read(folder / row['user'] / 'iperf.json')
    cmd = read(folder / row['user'] / 'command.json')
    user = topology['users'][row['user']]
    require(cmd[:6] == ['kubectl', '-n', 'free5gc', 'exec', user['pod'], '--'] and 'iperf3' in cmd and
            cmd[cmd.index('-B') + 1] == user['ip'] and cmd[cmd.index('-p') + 1] == str(route['nodePort']) and
            cmd[cmd.index('-c') + 1] == topology['target'] and cmd[cmd.index('-t') + 1] == '10' and
            cmd[cmd.index('-b') + 1] == '1M' and ('-R' in cmd) == (row['direction'] == 'downlink'),
            'Saved command binding/destination/load differs')
    start, end = data['start'], data['end']
    received, sent = end['sum_received'], end['sum_sent']
    require(not data.get('error') and 16384 <= received['bytes'] <= sent['bytes'] and received['seconds'] >= 9.5 and
            start['test_start']['duration'] == 10 and start['test_start']['reverse'] == int(row['direction'] == 'downlink') and
            start['connected'] and all(c['local_host'] == user['ip'] and c['remote_host'] == topology['target'] and
            c['remote_port'] == route['nodePort'] for c in start['connected']), 'Invalid payload/window/direction/UE socket')
    if row['direction'] == 'uplink':
        require(received['bytes'] == sent['bytes'], 'Uplink transfer byte totals differ')
    name = 'cnf-a-' + route['businessClass'] + '-' + route['user']
    correct = (folder / (name + '.txt')).read_text(encoding='utf-8')
    cookie = start['cookie']
    require(cookie and cookie in correct and 'Accepted connection from ' + topology['envoy']['ip'] + ',' in correct,
            'Expected backend cookie/Envoy peer missing')
    require(all(cookie not in (folder / (other + '.txt')).read_text(encoding='utf-8')
                for other in topology['backends'] if other != name), 'Cookie reached wrong backend')
    entries = []
    for line in (folder / 'envoy-access.txt').read_text(encoding='utf-8').splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if (item.get('experiment') == spec['configVersion'] and item.get('test_user') == row['user'] and
                stamp(row['startedAt']) <= stamp(item['start']) <= stamp(row['trafficEndedAt'])):
            entries.append(item)
    require(len(entries) == 2 and all(e['flags'] == '-' and e['business_class'] == route['businessClass'] and
            e['listener'] == name and e['cluster'] == name and e['local'].rsplit(':', 1)[-1] == str(route['listenPort']) and
            e['upstream'] == topology['services'][name]['spec']['clusterIP'] + ':5201' for e in entries),
            'Raw proxy route/user/connection evidence differs')
    field = 'bytes_sent' if row['direction'] == 'downlink' else 'bytes_received'
    require(max(int(e[field]) for e in entries) >= received['bytes'], 'Proxy payload counter missing')
    require(row['outcome'] == 'passed' and row['cookie'] == cookie and row['receivedBytes'] == received['bytes'],
            'Row differs from raw result')
    return cookie, received['bytes'], window(data)


def audit(root, actions):
    spec = read(ROOT / 'infra/cnfs/multi-ue-experiment.json')
    require(spec['configVersion'] == 'cnf-multi-ue-v1' and spec['durationSeconds'] == 10 and
            spec['offeredMbpsPerUE'] == 1 and spec['minimumOverlapSeconds'] == 8, 'Unexpected experiment parameters')
    require(len(set(actions.values())) == len(actions), 'Action folders must be distinct')
    for name in actions.values():
        require(Path(name).name == name and read(root / name / 'summary.json')['outcome'] == 'passed', 'Failed/invalid action')
    deployed = root / actions['deploy']
    original = read(deployed / 'original-pods.json')
    require(len(original['free5gc']) == 15 and len(original['cnf-lab']) == 3, 'Wrong original baseline')
    original_ids = ids(original)
    reference = read(deployed / 'live-after/pods.json')
    all_ids = ids(reference)
    require(len(all_ids) == 24 and all(all_ids[k] == v for k, v in original_ids.items()), 'Unexpected deployment/identity change')
    topology0 = read(deployed / 'live-after/topology.json')
    users0 = topology0['users']
    require({u['supi'] for u in users0.values()} == {'imsi-208930000000001', 'imsi-208930000000002'} and
            len({u['ip'] for u in users0.values()}) == 2 and all(u['node'] == 'ran-test' for u in users0.values()),
            'Distinct UE identity/session proof missing')
    manifests = list(yaml.safe_load_all((deployed / 'manifest.yaml').read_text(encoding='utf-8')))
    source_checks = checkpoints = 0
    inherited = read(ROOT / 'infra/cnfs/service-chain-profile.json')['inputSha256LF']
    expected_inputs = set(inherited) | {'infra/cnfs/multi-ue-experiment.json', 'scripts/cnf-multi-ue.py',
        'infra/free5gc-test-subscriber-ue2.json', 'scripts/create-test-subscriber.sh',
        'infra/cnfs/service-chain-profile.json', 'scripts/cnf-profile.py'}
    for phase, action in actions.items():
        meta = read(root / action / 'metadata.json')
        require(meta['stage'] == 'R2.S4' and meta['task'] == 'A' and meta['configVersion'] == spec['configVersion'] and
                meta['command'] == phase and set(meta['sha256LF']) == expected_inputs, 'Stage/action/input metadata differs')
        for relative, expected in meta['sha256LF'].items():
            path = (ROOT / relative).resolve()
            require(path.is_relative_to(ROOT) and hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest() == expected,
                    'Input hash differs: ' + relative)
            source_checks += 1
        require(list(yaml.safe_load_all((root / action / 'manifest.yaml').read_text(encoding='utf-8'))) == manifests,
                'Fixture manifest differs between phases')
        for path in (root / action).rglob('*pods.json'):
            state = read(path)
            actual = ids(state)
            require(all(actual.get(k) == v for k, v in original_ids.items()), 'Original Pod changed: ' + str(path))
            if 'cnf-multi-ue' in state:
                require(actual == all_ids, 'Measurement fixture changed: ' + str(path))
            checkpoints += 1
        for path in (root / action).rglob('topology.json'):
            require(read(path)['users'] == users0, 'UE identity/IP changed')
    cookies, samples, total_bytes, phases, overlaps = set(), [], 0, {}, []
    for mode in ['individual', 'smoke', 'concurrent']:
        folder = root / actions[mode]
        topology = read(folder / 'live-before/topology.json')
        require(ids(read(folder / 'before-pods.json')) == ids(read(folder / 'after-pods.json')) == all_ids, 'Batch identity differs')
        groups = read(folder / 'groups.json')
        require(len(groups) == {'individual': 8, 'smoke': 4, 'concurrent': 20}[mode], 'Wrong group count')
        counts = Counter()
        for group in groups:
            sub = folder / group['folder']
            require(Path(group['folder']).name == group['folder'] and read(sub / 'summary.json') ==
                    {k: v for k, v in group.items() if k != 'folder'}, 'Group summary differs')
            rows = group['rows']
            require(len(rows) == (1 if mode == 'individual' else 2), 'Missing UE in group')
            proxy_entries = []
            for line in (sub / 'envoy-access.txt').read_text(encoding='utf-8').splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get('experiment') == spec['configVersion'] and min(stamp(r['startedAt']) for r in rows) <= stamp(
                        entry['start']) <= max(stamp(r['trafficEndedAt']) for r in rows):
                    proxy_entries.append(entry)
            require(len(proxy_entries) == 2 * len(rows) and {e.get('test_user') for e in proxy_entries} ==
                    {r['user'] for r in rows}, 'Extra or unattributed proxy connections')
            if mode != 'individual':
                require({r['user'] for r in rows} == {'ue1', 'ue2'} and len({(r['businessClass'], r['direction'], r['round']) for r in rows}) == 1,
                        'Paired users are not the same class/direction/round')
            windows = []
            for row in rows:
                route = next(r for r in spec['routes'] if r['user'] == row['user'] and r['businessClass'] == row['businessClass'])
                cookie, payload, active = raw_flow(sub, row, route, topology, spec)
                require(cookie not in cookies, 'Cookie reused across phases')
                cookies.add(cookie)
                total_bytes += payload
                windows.append(active)
                counts[(row['user'], row['businessClass'], row['direction'], row['round'])] += 1
            if mode != 'individual':
                overlap = max(0, min(w[1] for w in windows) - max(w[0] for w in windows))
                require(overlap >= spec['minimumOverlapSeconds'] and abs(overlap - group['overlapLowerBoundSeconds']) < 1e-6,
                        'Concurrent positive-byte windows did not overlap')
                if mode == 'concurrent':
                    overlaps.append(overlap)
            require(ids(read(sub / 'guard-pods.json')) == all_ids, 'Traffic guard changed')
            memory = read(sub / 'resource-samples.json')
            require(memory and all(0 < s['upfMemoryBytes'] < 512 * 1024 ** 2 for s in memory), 'Unsafe/missing memory samples')
            samples.extend(memory)
            counters = []
            for label in ['before', 'after']:
                line = next(s for s in (sub / f'upf-{label}-interfaces.txt').read_text().splitlines() if s.strip().startswith('upfgtp:'))
                fields = line.split(':', 1)[1].split()
                counters.append((int(fields[0]), int(fields[8])))
            require(all(b > a for a, b in zip(*counters)), 'UPF counters did not increase')
        expected = Counter({(u, c, d, n): 1 for u in ['ue1', 'ue2'] for c in ['core', 'edge']
                            for d in ['uplink', 'downlink'] for n in range(1, 6 if mode == 'concurrent' else 2)})
        require(counts == expected, 'Missing, duplicated or extra measurement rows')
        phases[mode] = {'sessions': sum(counts.values()), 'groups': len(groups)}
    resource_reference = read(deployed / 'live-after/resources.json')
    resource_checks = 0
    for action in actions.values():
        for path in (root / action).rglob('resources.json'):
            if path.parent.name == 'original-profile':
                continue
            require(read(path) == resource_reference, 'Fixture spec/Service/policy identity changed')
            resource_checks += 1
    final = read(root / actions['check'] / 'live/pods.json')
    require(ids(final) == all_ids and all(p['phase'] == 'Running' and p['containers'] and
            all(c['ready'] and c['restartCount'] == 0 for c in p['containers']) for pods in final.values() for p in pods.values()),
            'Final readiness/restart check failed')
    return {'route': 'R2', 'stage': 'R2.S4', 'task': 'A', 'configVersion': spec['configVersion'], 'outcome': 'passed',
            'actions': actions, 'phases': phases, 'formalSessions': 40, 'formalProxyConnections': 80,
            'formalMinimumOverlapLowerBoundSeconds': min(overlaps), 'allPhaseUniqueCookies': len(cookies),
            'allPhaseReceivedBytes': total_bytes, 'originalPodsUnchanged': 18, 'totalStablePods': 24,
            'identityCheckpoints': checkpoints, 'fixtureResourceCheckpoints': resource_checks, 'sourceHashChecks': source_checks,
            'upfMemorySamples': len(samples), 'sampledPeakUpfMiB': max(s['upfMemoryBytes'] for s in samples) / 1024 ** 2,
            'users': users0, 'finalContainerRestarts': 0,
            'limitations': spec['limitations']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--deploy', default='deploy-01')
    parser.add_argument('--individual', default='individual-01')
    parser.add_argument('--smoke', default='smoke-01')
    parser.add_argument('--concurrent', default='concurrent-01')
    parser.add_argument('--check', default='final-check-01')
    args = parser.parse_args()
    result = audit(args.results, {key: getattr(args, key) for key in ['deploy', 'individual', 'smoke', 'concurrent', 'check']})
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
