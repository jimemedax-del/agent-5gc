#!/usr/bin/env python3
"""Offline audit of joint-check raw receivers, routes, queues and restoration."""
import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics

import yaml

ROOT = Path(__file__).resolve().parent.parent


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def need(value, message):
    if not value:
        raise ValueError(message)


def ids(state):
    result = {}
    for ns, pods in state.items():
        for name, pod in pods.items():
            need(pod['phase'] == 'Running' and pod['containers'] and all(c['ready'] and
                 c['restartCount'] == 0 and 'running' in c['state'] for c in pod['containers']), 'Unsafe Pod state')
            result[ns, name] = (pod['uid'], tuple((c['name'], c['containerID'], c['restartCount']) for c in pod['containers']))
    return result


def unaffected(state):
    return {key: value for key, value in ids(state).items() if not (key[0] == 'cnf-lab' and
            any(key[1].startswith(p + '-') for p in ['cnf-m1-backend', 'cnf-m2-edge']))}


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def raw_queue(data, shaped, rate):
    need(len(data) == 1 and data[0].get('root'), 'Unexpected queue inventory')
    q = data[0]
    if shaped:
        need(q['kind'] == 'tbf' and q['handle'] == '1:' and q['options']['rate'] == rate * 1e6 / 8 and
             q['options']['burst'] == '32Kb/1' and q['options']['lat'] == 100000, 'Wrong TBF parameters')
    else:
        need(q['kind'] == 'noqueue' and q['handle'] == '0:', 'Unexpected unshaped queue')
    return q


def audit(folder, repo=ROOT):
    hashes = read(folder / 'input-hashes.json')
    need(hashes and 'scripts/cnf-joint-check.py' in hashes and 'infra/cnfs/joint-check-experiment.json' in hashes,
         'Missing provenance')
    for name, expected in hashes.items():
        for path in [folder / 'inputs' / name, repo / name]:
            need(path.resolve().is_relative_to((folder / 'inputs').resolve()) if path == folder / 'inputs' / name
                 else path.resolve().is_relative_to(repo.resolve()), 'Unsafe source path')
            actual = hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            need(actual == expected, 'Source/input changed: ' + name)
    tool_spec = importlib.util.spec_from_file_location('joint_audit_collector', repo / 'scripts/cnf-joint-check.py')
    tool = importlib.util.module_from_spec(tool_spec)
    tool_spec.loader.exec_module(tool)
    spec = read(folder / 'experiment.json')
    need(spec == tool.config(), 'Frozen experiment differs')
    routing = read(folder / 'routing.json')
    need(routing == tool.m2.render()[0], 'Frozen routing differs')
    need(list(yaml.safe_load_all((folder / 'joint-backends.yaml').read_text(encoding='utf-8'))) ==
         [d for d in tool.render() if d['kind'] == 'Deployment' and d['metadata']['name'] in tool.BACKENDS], 'Joint manifest differs')
    original = [d for d in tool.base.render('protected') if d['kind'] == 'Deployment' and d['metadata']['name'] in tool.BACKENDS]
    need(list(yaml.safe_load_all((folder / 'restore-backends.yaml').read_text(encoding='utf-8'))) == original and
         list(yaml.safe_load_all((folder / 'restoration/restore.yaml').read_text(encoding='utf-8'))) == original,
         'Restore manifest differs')
    initial = read(folder / 'initial/observed/observation.json')
    final = read(folder / 'final/observed/observation.json')
    need(unaffected(initial['pods']) == unaffected(final['pods']), 'Unaffected Pods replaced')
    for field in ['resources', 'bindings', 'queueMode', 'queueShape', 'uplinkClean', 'nativeQos', 'endpoint']:
        need(initial[field] == final[field], 'Final state differs: ' + field)
    need(initial['queueMode'] == 'none' and initial['uplinkClean'] and initial['nativeQos'] == 'QoS Enable: 0',
         'Unrelated shaping/native QoS active')
    state = read(folder / 'formal-pods.json')
    expected_ids = ids(state)
    need(unaffected(state) == unaffected(initial['pods']), 'Unaffected setup changed')
    envoy = next(p for n, p in state['cnf-lab'].items() if n.startswith('cnf-m1-envoy-'))
    runtime = read(folder / 'runtime.json')
    all_cookies, all_tokens, all_samples, total_bytes, results = set(), set(), [], 0, {}
    for phase in spec['phases']:
        parent = folder / phase['name']
        rows = read(parent / 'rounds.json')
        need(len(rows) == 2 * phase['roundsPerClass'], 'Wrong number of sessions')
        rates = {name: [] for name in spec['ratesMbps']}
        for checkpoint in ['before', 'after']:
            topology = parent / checkpoint
            need(ids(read(topology / 'pods.json')) == expected_ids, 'Phase identity drift')
            need((topology / 'qos-switch.txt').read_text().strip() == 'QoS Enable: 0', 'Native QoS enabled')
            resources = read(topology / 'resources.json')
            expected_resources = {(d['kind'], d['metadata']['name']): d for d in tool.render()}
            need({(r['kind'], r['name']) for r in resources} == set(expected_resources), 'Resource set differs')
            for item in resources:
                wanted = expected_resources[item['kind'], item['name']]
                tool.m3.require_subset(wanted.get('data') if item['kind'] == 'ConfigMap' else wanted['spec'], item['body'])
                if item['kind'] == 'NetworkPolicy':
                    need(item['body'] == wanted['spec'], 'Policy differs')
            for route in routing['classes']:
                pod_name, pod = next((n, p) for n, p in state['cnf-lab'].items()
                                    if n.startswith(route['backendDeployment'] + '-'))
                deployment = next(r['body'] for r in resources if r['kind'] == 'Deployment' and r['name'] == route['backendDeployment'])
                selector = tool.m4.render(True)[0]['edgeNodeSelector'] if route['name'] == 'edge' else {'5gc.free5gc.org/plane': 'control'}
                need(deployment['template']['spec']['nodeSelector'] == selector, 'Placement selector differs')
                nodes = read(topology / 'nodes.json')
                matching = [n['name'] for n in nodes if all(n['labels'].get(k) == v for k, v in selector.items())]
                need(matching == [pod['node']], 'Actual node differs')
                tool.m4.validate_endpoint(read(topology / (route['name'] + '-endpoints.json')),
                    read(topology / (route['name'] + '-endpointslices.json')), pod_name, pod, pod['node'])
                for container in pod['containers']:
                    tool.m1.verify_image_lock({'containers': [container]}, tool.m1.config()[1]['images']['backend']['image'])
                raw_queue(read(topology / (route['name'] + '-qdisc.json')), phase['shaped'], spec['ratesMbps'][route['name']])
        for index, row in enumerate(rows):
            name = row['businessClass']
            route = routing['classes'][index % 2]
            need(name == route['name'] and row['round'] == index // 2 + 1 and row['phase'] == phase['name'], 'Wrong session order')
            run = parent / f'{row["round"]:02d}-{name}'
            need(read(run / 'summary.json') == row and row['outcome'] == 'passed', 'Session failed/summary differs')
            data, command = read(run / 'iperf.json'), read(run / 'command.json')
            source = read(parent / 'ue-address.json')
            ip = next(a['local'] for d in source for a in d['addr_info'] if a['family'] == 'inet')
            need(command[:3] == ['kubectl', '-n', 'free5gc'] and '-R' in command and
                 all(command[command.index(flag) + 1] == value for flag, value in
                     [('-p', str(route['nodePort'])), ('-B', ip), ('-P', '1'), ('-b', '20M'), ('-t', '15'), ('-l', '16384')]),
                 'Workload differs')
            need('dev uesimtun0' in (parent / 'ue-route.txt').read_text(), 'UE route bypasses TUN')
            token = command[command.index('--extra-data') + 1]
            need(token not in all_tokens and len(token) == 24 and data['extra_data'] == token, 'Acquisition token differs')
            all_tokens.add(token)
            received, sent = data['end']['sum_received'], data['end']['sum_sent']
            need(not data.get('error') and data['start']['test_start']['reverse'] == 1 and
                 0 < received['bytes'] <= sent['bytes'] and received['seconds'] >= 14.25, 'Invalid receiver data')
            mbps = received['bytes'] * 8 / received['seconds'] / 1e6
            need(math.isfinite(mbps) and abs(mbps - row['receiverMbps']) < 1e-8, 'Raw receiver mismatch')
            rates[name].append(mbps)
            total_bytes += received['bytes']
            cookie = data['start']['cookie']
            need(cookie not in all_cookies, 'Repeated cookie')
            all_cookies.add(cookie)
            backend = (run / 'expected-backend.txt').read_text(encoding='utf-8')
            need(cookie in backend and cookie not in (run / 'other-backend.txt').read_text(encoding='utf-8') and
                 'Accepted connection from ' + envoy['ip'] + ',' in backend, 'Backend identity mismatch')
            entries = []
            for line in (run / 'envoy-access.txt').read_text(encoding='utf-8').splitlines():
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if (isinstance(item, dict) and item.get('experiment') == routing['configVersion'] and
                        stamp(row['startedAt']) <= stamp(item['start']) <= stamp(row['trafficEndedAt'])):
                    entries.append(item)
            service_ip = runtime['services'][route['backendService']]['clusterIP']
            need(len(entries) == 2 and all(e['flags'] == '-' and e['business_class'] == name and
                 e['listener'] == route['listener'] and e['cluster'] == route['cluster'] and
                 e['upstream'] == service_ip + ':5201' for e in entries) and
                 max(int(e['bytes_sent']) for e in entries) >= received['bytes'], 'Proxy evidence mismatch')
            queues = [raw_queue(read(run / ('qdisc-' + label + '.json')), phase['shaped'], spec['ratesMbps'][name])
                      for label in ['before', 'after']]
            if phase['shaped']:
                a, b = queues
                for key in ['bytes', 'packets', 'drops', 'overlimits', 'requeues']:
                    need(b[key] - a[key] == row['qdiscDelta'][key] >= 0, 'Queue counter mismatch')
                need(b['bytes'] - a['bytes'] >= received['bytes'] and b['overlimits'] > a['overlimits'], 'No shaping evidence')
            counters = []
            for label in ['before', 'after']:
                line = next(s for s in (run / ('upf-' + label + '-interfaces.txt')).read_text().splitlines()
                            if s.strip().startswith('upfgtp:'))
                fields = line.split(':')[1].split()
                counters.append((int(fields[0]), int(fields[8])))
                policies = read(run / (label + '-policies.json'))
                wanted = {d['metadata']['name']: d['spec'] for d in tool.np.render()[5]}
                need({p['metadata']['name']: p['spec'] for p in policies} == wanted, 'Per-session policy changed')
            need(all(b > a for a, b in zip(*counters)), 'UPF counters missing')
            need(ids(read(run / 'guard-pods.json')) == expected_ids, 'Guard identity drift')
            samples = read(run / 'resource-samples.json')
            need(samples and all(0 < s['upfMemoryBytes'] < 536870912 for s in samples), 'Unsafe/missing memory samples')
            all_samples.extend(samples)
            need(read(run / 'cleanup.json')['returncode'] in [0, 1], 'Process cleanup failed')
        medians = {name: statistics.median(values) for name, values in rates.items()}
        if phase['shaped']:
            need(all(.8 * spec['ratesMbps'][name] <= v <= 1.15 * spec['ratesMbps'][name] for name, v in medians.items()) and
                 medians['edge'] / medians['core'] >= 2, 'Shaped acceptance failed')
        else:
            need(all(v >= 18 for v in medians.values()), 'Unshaped acceptance failed')
        phase_summary = read(parent / 'summary.json')
        need(phase_summary['outcome'] == 'passed' and phase_summary['medianReceiverMbps'] == medians, 'Phase summary mismatch')
        results[phase['name']] = medians
    summary = read(folder / 'summary.json')
    need(summary['outcome'] == 'passed' and summary['restoration'] == 'passed' and summary['strictFinal'] == 'passed',
         'No full restored acceptance')
    for phase, medians in results.items():
        need(summary['phases'][phase]['medianReceiverMbps'] == medians, 'Final summary mismatch')
    need(read(folder / 'restoration/summary.json')['outcome'] == 'passed' and
         read(folder / 'restoration/protected/state.json')['state'] == 'protected', 'Not restored protected')
    need(unaffected(read(folder / 'restoration/final-pods.json')) == unaffected(initial['pods']), 'Restoration identity drift')
    for name in ['initial-protected', 'restoration/protected']:
        resources = read(folder / name / 'resources.json')
        tool.base.match('protected', {(r['kind'], r['name']): {'kind': r['kind'], 'metadata': {'name': r['name']},
                        'data' if r['kind'] == 'ConfigMap' else 'spec': r['body']} for r in resources})
    report = read(folder / 'restoration/strict-final/verify-report.json')
    need(len(report['workloads']) == 15 and not report['allowTagBaseline'] and
         '5 packets transmitted, 5 received, 0% packet loss' in report['ping'], 'Strict final baseline failed')
    return {'outcome': 'passed_offline_joint_audit', 'configVersion': spec['configVersion'],
            'sessions': len(all_cookies), 'proxyConnections': len(all_cookies) * 2,
            'medianReceiverMbps': results, 'totalReceivedBytes': total_bytes,
            'formalPodsStable': len(expected_ids), 'unaffectedPodsStableAcrossSetupAndRestore': len(unaffected(state)),
            'sourceHashChecks': len(hashes), 'upfMemorySamples': len(all_samples),
            'sampledPeakUpfMiB': max(s['upfMemoryBytes'] for s in all_samples) / 1024 ** 2,
            'restoredState': 'protected'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.results)
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
