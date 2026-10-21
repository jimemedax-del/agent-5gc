#!/usr/bin/env python3
"""Independently audit R2.S3 recovery, rebuild, idempotence and repeated raw measurements."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics

import yaml


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def require(value, reason):
    if not value:
        raise ValueError(reason)


def ids(state):
    return {(ns, n): (p['uid'], tuple((c['name'], c['containerID'], c['restartCount']) for c in p['containers']))
            for ns, pods in state.items() for n, p in pods.items()}


def one(state, prefix):
    return next(p for n, p in state['cnf-lab'].items() if n.startswith(prefix + '-'))


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def manifest(path):
    return list(yaml.safe_load_all(path.read_text(encoding='utf-8')))


def sha_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def raw_flow(folder, row, routing, runtime, envoy_ip, direction, measurement_state):
    raw = read(folder / 'iperf.json')
    sent, received = raw['end']['sum_sent'], raw['end']['sum_received']
    require(not raw.get('error') and 0 < received['bytes'] <= sent['bytes'] and
            raw['start']['test_start']['reverse'] == int(direction == 'downlink'), 'Invalid raw payload/direction')
    cookie = raw['start']['cookie']
    require(cookie in (folder / 'expected-backend.txt').read_text(encoding='utf-8') and
            cookie not in (folder / 'other-backend.txt').read_text(encoding='utf-8'), 'Cookie at wrong backend')
    require('Accepted connection from ' + envoy_ip + ',' in (folder / 'expected-backend.txt').read_text(encoding='utf-8'),
            'Backend did not identify Envoy peer')
    entries = []
    for line in (folder / 'envoy-access.txt').read_text(encoding='utf-8').splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if (isinstance(item, dict) and item.get('experiment') == routing['configVersion'] and
                stamp(row['startedAt']) <= stamp(item['start']) <= stamp(row['trafficEndedAt'])):
            entries.append(item)
    route = next(r for r in routing['classes'] if r['name'] == row['businessClass'])
    byte_field = 'bytes_sent' if direction == 'downlink' else 'bytes_received'
    require(len(entries) == 2 and all(e['flags'] == '-' and e['business_class'] == route['name'] and
            e['cluster'] == route['cluster'] and e['listener'] == route['listener'] and
            e['upstream'] == runtime[route['backendService']]['spec']['clusterIP'] + ':5201' for e in entries) and
            max(int(e[byte_field]) for e in entries) >= received['bytes'], 'Proxy route/payload evidence differs')
    require(ids(read(folder / 'guard-pods.json')) == ids(measurement_state), 'Measurement guard identity changed')
    samples = read(folder / 'resource-samples.json')
    require(samples and all(0 < s['upfMemoryBytes'] < 512 * 1024 ** 2 for s in samples), 'Missing/unsafe memory samples')
    counters = []
    for label in ['before', 'after']:
        line = next(s for s in (folder / f'upf-{label}-interfaces.txt').read_text().splitlines() if s.strip().startswith('upfgtp:'))
        fields = line.split(':')[1].split()
        counters.append((int(fields[0]), int(fields[8])))
    require(all(b > a for a, b in zip(*counters)), 'UPF did not record bidirectional traffic')
    require(row['outcome'] == 'passed', 'A round failed')
    return cookie, received['bytes'], received['bytes'] * 8 / received['seconds'] / 1e6, samples


def audit(root, repo):
    actions = ['check-01', 'apply-idempotent-01', 'recovery-01', 'protected-measure-01', 'rebuild-01',
               'apply-idempotent-02', 'qos-apply-01', 'qos-measure-01', 'restore-01',
               'placed-apply-01', 'placed-restore-01', 'protected-measure-02', 'apply-idempotent-03']
    for action in actions:
        require(read(root / action / 'summary.json')['outcome'] == 'passed', 'Incomplete/failed action: ' + action)
    baseline = read(root / 'check-01/live/pods.json')
    core_ids = ids({'free5gc': baseline['free5gc']})
    checkpoints = 0
    for path in root.glob('**/*pods.json'):
        state = read(path)
        if 'free5gc' in state:
            require(ids({'free5gc': state['free5gc']}) == core_ids, '5GC identity changed: ' + str(path))
            checkpoints += 1
    for action in ['apply-idempotent-01', 'apply-idempotent-02', 'apply-idempotent-03']:
        folder = root / action
        require(ids(read(folder / 'before-pods.json')) == ids(read(folder / 'after-pods.json')) and
                read(folder / 'summary.json')['evidence']['idempotent'], 'Repeated apply replaced/restarted Pods')
    recovery = root / 'recovery-01'
    require(read(recovery / 'recovery.json')['outcome'] == 'passed' and read(recovery / 'recovery.json')['intentional'],
            'Missing intentional rollback proof')
    require(read(recovery / 'live-applied/state.json')['state'] == 'neutral' and
            read(recovery / 'live-restored/state.json')['state'] == 'protected', 'Real state transition/recovery missing')
    require(one(read(recovery / 'live-applied/pods.json'), 'cnf-m2-edge')['node'] !=
            one(read(recovery / 'live-restored/pods.json'), 'cnf-m2-edge')['node'], 'Recovery never moved edge back')
    rebuild = root / 'rebuild-01'
    old, new = read(rebuild / 'before-pods.json'), read(rebuild / 'after-pods.json')
    for name in ['cnf-m1-backend', 'cnf-m2-edge', 'cnf-m1-envoy']:
        require(one(old, name)['uid'] != one(new, name)['uid'], 'A CNF was not actually rebuilt')
    old_services, new_services = [
        {r['name']: (r['uid'], r['body']['clusterIP'], r['body']['ports'])
         for r in read(rebuild / f'{p}/resources.json') if r['kind'] == 'Service'} for p in ['live-before', 'live-applied']]
    require(old_services == new_services and len(old_services) == 3, 'Rebuild changed Services/VIPs/ports')
    service_checkpoints = 0
    for path in root.glob('*/live*/resources.json'):
        services = {r['name']: (r['uid'], r['body']['clusterIP'], r['body']['ports'])
                    for r in read(path) if r['kind'] == 'Service'}
        require(services == old_services, 'A state transition changed Services/VIPs/ports')
        service_checkpoints += 1
    protected_manifest = manifest(root / 'check-01/manifest.yaml')
    backup = read(root / 'qos-apply-01/snapshot.json')
    require(backup['state'] == 'protected' and backup['manifestSha256'] == sha_json(protected_manifest), 'Invalid restore snapshot')
    require(manifest(root / 'restore-01/manifest.yaml') == protected_manifest and
            read(root / 'restore-01/live-applied/state.json')['state'] == 'protected', 'Snapshot restore differs')
    require(read(root / 'placed-apply-01/live-applied/state.json')['state'] == 'placed' and
            read(root / 'placed-restore-01/live-applied/state.json')['state'] == 'protected', 'Fourth state was not exercised')
    require(ids(read(root / 'placed-apply-01/before-pods.json')) == ids(read(root / 'placed-apply-01/after-pods.json')),
            'Policy-only state change replaced/restarted Pods')
    routing = read(repo / 'infra/cnfs/m2-experiment.json')
    cookies, samples, total_payload, batches = set(), [], 0, {}
    for action in ['protected-measure-01', 'protected-measure-02']:
        folder = root / action
        before, after = read(folder / 'before-pods.json'), read(folder / 'after-pods.json')
        require(ids(before) == ids(after), 'Protected batch changed Pod identity')
        require(manifest(folder / 'manifest.yaml') == protected_manifest, 'Repeated batch configuration differs')
        measuring = read(folder / 'measurement/measurement-pods.json')
        np_before, np_after = [read(folder / 'measurement' / f'policies-{s}-measure.json') for s in ['before', 'after']]
        require({p['metadata']['name']: (p['metadata']['uid'], p['spec']) for p in np_before} ==
                {p['metadata']['name']: (p['metadata']['uid'], p['spec']) for p in np_after}, 'Policy changed during measurement')
        state = read(folder / 'live-before/pods.json')
        envoy = one(state, 'cnf-m1-envoy')
        runtime = {r['name']: {'spec': r['body']} for r in read(folder / 'live-before/resources.json') if r['kind'] == 'Service'}
        checks = read(folder / 'measurement/isolation/connections.json')
        require(len(checks) == 45 and sum(r['expectedAllowed'] for r in checks) == 15, 'Incomplete isolation matrix')
        for number in range(1, 4):
            raw_checks = [read(p) for p in (folder / 'measurement/isolation' / f'{number:02d}').glob('*.json')]
            tcp = [c for c in raw_checks if 'sourcePod' in c]
            require(len(tcp) == 15, 'Missing raw TCP checks')
            for c in tcp:
                code = c['returnCode']
                require(code == 0 if c['expectedAllowed'] else code == 124 or code == 1 and any(t in c['stderr'] for t in
                    ['Connection refused', 'Connection timed out', 'No route to host', 'Network is unreachable']), 'Raw allow/deny mismatch')
            for name in ['core', 'edge']:
                dns = read(folder / 'measurement/isolation' / f'{number:02d}' / f'dns-{name}.json')
                service = next(r['backendService'] for r in routing['classes'] if r['name'] == name)
                require(dns['returnCode'] == 0 and {s.split()[0] for s in dns['stdout'].splitlines()} ==
                        {runtime[service]['spec']['clusterIP']}, 'DNS differs')
        rows = read(folder / 'measurement/business/rounds.json')
        require(len(rows) == 20, 'Incomplete protected business batch')
        counts = {}
        for row in rows:
            name, direction = row['businessClass'], row['direction']
            counts[name + '/' + direction] = counts.get(name + '/' + direction, 0) + 1
            run = folder / 'measurement/business' / f'{row["round"]:02d}-{direction}-{name}'
            require(read(run / 'summary.json') == row, 'Per-round summary differs')
            cookie, payload, _, memory = raw_flow(run, row, routing, runtime, envoy['ip'], direction, measuring)
            require(cookie not in cookies, 'Session cookie reused across independent batches')
            cookies.add(cookie)
            samples.extend(memory)
            total_payload += payload
        require(set(counts) == {'core/uplink', 'core/downlink', 'edge/uplink', 'edge/downlink'} and
                all(v == 5 for v in counts.values()), 'Wrong business class/direction counts')
        batches[action] = {'sessions': len(rows), 'counts': counts, 'allowed': 15, 'denied': 30}
    qos = root / 'qos-measure-01'
    require(ids(read(qos / 'before-pods.json')) == ids(read(qos / 'after-pods.json')), 'QoS measurement changed identities')
    state = read(qos / 'live-before/pods.json')
    envoy = one(state, 'cnf-m1-envoy')
    runtime = {r['name']: {'spec': r['body']} for r in read(qos / 'live-before/resources.json') if r['kind'] == 'Service'}
    values = {'core': [], 'edge': []}
    for row in read(qos / 'measurement/rounds.json'):
        name = row['businessClass']
        folder = qos / 'measurement' / f'{row["round"]:02d}-{name}'
        cookie, payload, mbps, memory = raw_flow(folder, row, routing, runtime, envoy['ip'], 'downlink', state)
        require(cookie not in cookies, 'Cookie reused in QoS batch')
        cookies.add(cookie)
        samples.extend(memory)
        total_payload += payload
        a, b = [next(q for q in read(folder / f'qdisc-{label}.json') if q.get('root')) for label in ['before', 'after']]
        data = read(folder / 'iperf.json')
        require(data['start']['test_start']['duration'] == 15 and data['end']['sum_received']['seconds'] >= 14.25,
                'QoS round did not complete its requested window')
        cap = 5 if name == 'core' else 15
        require(all(q['kind'] == 'tbf' and q['options']['rate'] * 8 == cap * 1000000 for q in [a, b]) and
                b['bytes'] - a['bytes'] >= payload and b['overlimits'] > a['overlimits'], 'QoS counter/rate proof missing')
        values[name].append(mbps)
    require(all(len(v) == 5 for v in values.values()), 'Incomplete QoS batch')
    medians = {k: statistics.median(v) for k, v in values.items()}
    ratio = medians['edge'] / medians['core']
    require(ratio >= 2 and medians['core'] <= 5.75 and medians['edge'] <= 17.25, 'QoS thresholds failed')
    hash_count = 0
    for meta in root.glob('*/metadata.json'):
        record = read(meta)
        require(record['stage'] == 'R2.S3', 'Incorrect stage metadata')
        for relative, expected in record['sha256LF'].items():
            require(hashlib.sha256((repo / relative).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == expected,
                    'Source hash differs: ' + relative)
            hash_count += 1
    require(read(root / 'apply-idempotent-03/live-applied/state.json')['state'] == 'protected', 'Final state is not protected')
    final = read(root / 'apply-idempotent-03/after-pods.json')
    require(len(final['cnf-lab']) == 3 and all(c['ready'] and c['restartCount'] == 0 for pods in final.values()
                for p in pods.values() for c in p['containers']), 'Final readiness/restart/cleanup failure')
    return {'route': 'R2', 'stage': 'R2.S3', 'configVersion': 'cnf-service-chain-v1', 'outcome': 'passed',
            'protectedBatches': batches, 'qosMedianMbps': medians, 'qosMedianRatio': ratio,
            'uniqueCookies': len(cookies), 'proxyConnections': 2 * len(cookies), 'totalReceivedBytes': total_payload,
            'original5gcPodsUnchanged': len(baseline['free5gc']), 'coreIdentityCheckpoints': checkpoints,
            'rebuildCNFs': 3, 'idempotentApplies': 3, 'snapshotRestore': True, 'intentionalRollback': True,
            'serviceIdentityCheckpoints': service_checkpoints, 'finalContainerRestarts': 0,
            'sourceHashChecks': hash_count, 'upfMemorySamples': len(samples),
            'sampledPeakUpfMiB': max(s['upfMemoryBytes'] for s in samples) / 1024 ** 2, 'finalState': 'protected'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.results, Path(__file__).resolve().parent.parent)
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
