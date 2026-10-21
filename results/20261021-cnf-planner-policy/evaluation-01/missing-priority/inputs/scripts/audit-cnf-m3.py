#!/usr/bin/env python3
"""Offline audit of saved M3 raw evidence; never accesses or changes the cluster."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def check(condition, message):
    if not condition:
        raise ValueError(message)


def identities(state):
    return {(ns, name): (pod['uid'], tuple((c['name'], c['containerID'], c['restartCount'])
            for c in pod['containers'])) for ns, pods in state.items() for name, pod in pods.items()}


def when(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def audit(folder, repo):
    accepted = folder / 'measure-01'
    spec = read(accepted / 'experiment.json')
    routing = read(accepted / 'routing.json')
    state = read(accepted / 'before-measure-pods.json')
    final = read(accepted / 'after-measure-pods.json')
    expected_ids = identities(state)
    check(expected_ids == identities(final), 'Final Pod/container identities changed')
    check(all(c['restartCount'] == 0 and c['ready'] for pods in final.values()
              for p in pods.values() for c in p['containers']), 'Final readiness/restart failure')
    initial = read(folder / 'preflight-01/before-measure-pods.json')
    check(identities({'free5gc': initial['free5gc']}) == identities({'free5gc': final['free5gc']}),
          'Original 5GC identities changed across setup/measurement')
    envoy_name, envoy = next((n, p) for n, p in state['cnf-lab'].items() if n.startswith('cnf-m1-envoy-'))
    check(initial['cnf-lab'][envoy_name]['uid'] == envoy['uid'], 'Envoy was replaced during setup')
    services = read(accepted / 'services.json')
    rows = read(accepted / 'rounds.json')
    check(len(rows) == 2 * spec['roundsPerClass'], 'Wrong number of raw rounds')
    cookies, rates, samples, proxy_count, total_bytes = set(), {}, [], 0, 0
    deployment_specs = []
    for route in routing['classes']:
        name = route['name']
        removed = read(folder / 'deploy-01' / (name + '-qdisc-removed.json'))
        reapplied = read(folder / 'deploy-01' / (name + '-qdisc-reapplied.json'))
        check(not any(q['kind'] == 'tbf' for q in removed) and
              any(q['kind'] == 'tbf' and q['options']['rate'] * 8 == spec['ratesMbps'][name] * 1000000
                  for q in reapplied), 'qdisc removal/reapplication evidence missing')
        live = read(accepted / (name + '-deployment.json'))['spec']['template']['spec']
        deployment_specs.append(live)
        check(live['nodeSelector'] == {'5gc.free5gc.org/plane': 'control'}, 'Wrong placement')
        check({c['name'] for c in live['containers']} == {'iperf3', 'tc'}, 'Unexpected backend containers')
        for c in live['containers']:
            check('@sha256:' in c['image'] and not c['securityContext'].get('privileged'), 'Image/privilege drift')
            check(c['securityContext']['capabilities'] ==
                  ({'drop': ['ALL'], 'add': ['NET_ADMIN']} if c['name'] == 'tc' else {'drop': ['ALL']}),
                  'Unexpected container capabilities')
        rates[name] = []
        for number in range(1, spec['roundsPerClass'] + 1):
            run = accepted / f'{number:02d}-{name}'
            row = read(run / 'summary.json')
            check(row == next(r for r in rows if r['round'] == number and r['businessClass'] == name),
                  'Per-round summary differs from batch rows')
            data = read(run / 'iperf.json')
            command = read(run / 'command.json')
            check('-R' in command and command[command.index('-t') + 1] == str(spec['durationSeconds']) and
                  command[command.index('-b') + 1] == str(spec['offeredMbps']) + 'M' and
                  command[command.index('-p') + 1] == str(route['nodePort']), 'Wrong workload')
            check(not data.get('error') and data['start']['test_start']['reverse'] == 1, 'iperf error or wrong direction')
            received = data['end']['sum_received']
            sent = data['end']['sum_sent']
            check(received['bytes'] > 0 and sent['bytes'] >= received['bytes'] and
                  received['seconds'] >= .95 * spec['durationSeconds'], 'Incomplete receiver measurement')
            mbps = received['bytes'] * 8 / received['seconds'] / 1e6
            check(abs(mbps - row['receiverMbps']) < 1e-8, 'Summary receiver rate differs from raw bytes/time')
            rates[name].append(mbps)
            total_bytes += received['bytes']
            before, after = read(run / 'qdisc-before.json'), read(run / 'qdisc-after.json')
            a, b = [next(q for q in items if q.get('root')) for items in [before, after]]
            check(all(q['kind'] == 'tbf' and q['handle'] == '1:' and
                      q['options']['rate'] * 8 == spec['ratesMbps'][name] * 1000000 for q in [a, b]), 'Wrong TBF rate')
            for key in ['bytes', 'packets', 'drops', 'overlimits', 'requeues']:
                check(b[key] - a[key] == row['qdiscDelta'][key] >= 0, 'qdisc delta mismatch')
            check(b['bytes'] - a['bytes'] >= received['bytes'] and b['overlimits'] > a['overlimits'], 'TBF activity missing')
            cookie = data['start']['cookie']
            check(cookie not in cookies, 'Duplicate cookie')
            cookies.add(cookie)
            backend = (run / 'expected-backend.txt').read_text(encoding='utf-8')
            wrong = (run / 'other-backend.txt').read_text(encoding='utf-8')
            check(cookie in backend and cookie not in wrong and 'Accepted connection from ' + envoy['ip'] + ',' in backend,
                  'Backend cookie/peer mismatch')
            logs = []
            for line in (run / 'envoy-access.txt').read_text(encoding='utf-8').splitlines():
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if (isinstance(item, dict) and item.get('experiment') == routing['configVersion'] and
                        when(row['startedAt']) <= when(item['start']) <= when(row['trafficEndedAt'])):
                    logs.append(item)
            check(len(logs) == 2 and all(x['flags'] == '-' and x['business_class'] == name and
                  x['listener'] == route['listener'] and x['cluster'] == route['cluster'] and
                  x['upstream'] == services[route['backendService']]['spec']['clusterIP'] + ':5201'
                  for x in logs) and max(int(x['bytes_sent']) for x in logs) >= received['bytes'],
                  'Downlink proxy evidence mismatch')
            proxy_count += len(logs)
            check(identities(read(run / 'guard-pods.json')) == expected_ids, 'Guard detected identity drift')
            batch_samples = read(run / 'resource-samples.json')
            check(batch_samples and all(0 < s['upfMemoryBytes'] < 512 * 1024 ** 2 for s in batch_samples), 'Missing/unsafe memory samples')
            samples.extend(batch_samples)
            counters = []
            for label in ['before', 'after']:
                line = next(s for s in (run / f'upf-{label}-interfaces.txt').read_text().splitlines()
                            if s.strip().startswith('upfgtp:'))
                fields = line.split(':')[1].split()
                counters.append((int(fields[0]), int(fields[8])))
            check(all(y > x for x, y in zip(*counters)), 'UPF bidirectional counter evidence missing')
    # Server/helper settings are identical except the intended rate in the helper command.
    left, right = deployment_specs
    check(left['securityContext'] == right['securityContext'] and left['volumes'] == right['volumes'], 'Pod setup differs')
    for a, b in zip(left['containers'], right['containers']):
        check(all(a[k] == b[k] for k in ['image', 'resources', 'securityContext']), 'Backend resources/images/security differ')
    medians = {k: statistics.median(v) for k, v in rates.items()}
    ratio = medians['edge'] / medians['core']
    check(ratio >= spec['minimumMedianRatio'], 'Median ratio below threshold')
    check(all(v <= spec['ratesMbps'][k] * (1 + spec['maximumMedianExcessFraction']) for k, v in medians.items()), 'Median cap exceeded')
    summary = read(accepted / 'summary.json')
    check(summary['outcome'] == 'passed' and summary['evidence']['fullAcceptance'], 'No full acceptance')
    check(abs(summary['evidence']['medianRatio'] - ratio) < 1e-8, 'Summary ratio differs')
    for name in rates:
        check(abs(summary['evidence']['classes'][name]['medianReceiverMbps'] - medians[name]) < 1e-8,
              'Summary median differs from raw receiver bytes/time')
    hash_checks = 0
    for metadata in folder.glob('*/metadata.json'):
        for filename, expected in read(metadata)['sha256LF'].items():
            candidate = repo / filename
            digest = lambda p: hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            if digest(candidate) != expected and filename == 'scripts/cnf-m3.py':
                candidate = folder / 'collector-sources' / metadata.parent.name.split('-')[0] / 'cnf-m3.py'
            check(digest(candidate) == expected, 'Source hash differs: ' + str(metadata) + '/' + filename)
            hash_checks += 1
    return {'route': 'R2', 'stage': 'R2.S2', 'milestone': 'M3', 'configVersion': spec['configVersion'],
            'outcome': 'passed', 'acceptedBatch': 'measure-01', 'receiverMbps': rates, 'medianReceiverMbps': medians,
            'medianRatio': ratio, 'uniqueCookies': len(cookies), 'proxyConnections': proxy_count,
            'totalReceivedBytes': total_bytes, 'upfMemorySamples': len(samples),
            'sampledPeakUpfMiB': max(s['upfMemoryBytes'] for s in samples) / 1024 ** 2,
            'original5gcPodsUnchanged': len(initial['free5gc']), 'finalContainerRestarts': 0,
            'sourceHashChecks': hash_checks}


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
