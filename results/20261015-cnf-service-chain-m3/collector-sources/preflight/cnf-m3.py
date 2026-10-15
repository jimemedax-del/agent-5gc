#!/usr/bin/env python3
"""Fixed R2.S2 M3: backend Pod egress shaping and guarded TCP downlink evidence."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / 'infra/cnfs/m3-experiment.json'
module = importlib.util.spec_from_file_location('cnf_m2', ROOT / 'scripts/cnf-m2.py')
m2 = importlib.util.module_from_spec(module)
module.loader.exec_module(m2)
m1 = m2.m1


def tc_command(spec, name):
    q = spec['qdisc']
    return ['tc', 'qdisc', 'add', 'dev', q['interface'], 'root', 'handle', q['handle'],
            q['kind'], 'rate', str(spec['ratesMbps'][name]) + 'mbit',
            'burst', q['burst'], 'latency', q['latency']]


def render():
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    routing, images, inherited = m2.render()
    if spec['inheritedRouting'] != routing['configVersion']:
        raise ValueError('Unexpected inherited routing version')
    original = m1.config()[0]
    core = next(d for d in original if d['kind'] == 'Deployment' and d['metadata']['name'] == 'cnf-m1-backend')
    edge = next(d for d in inherited if d['kind'] == 'Deployment' and d['metadata']['name'] == 'cnf-m2-edge')
    docs = copy.deepcopy([core, edge])
    for route, doc in zip(routing['classes'], docs):
        pod = doc['spec']['template']
        pod['metadata'].setdefault('annotations', {})['cnf.lab/shaping-version'] = spec['configVersion']
        doc['spec']['strategy'] = {'type': 'Recreate'}
        pod['spec']['containers'].append({
            'name': 'tc', 'image': images['images']['backend']['image'], 'imagePullPolicy': 'Never',
            'command': ['sh', '-ec', ' '.join(tc_command(spec, route['name'])) + '; exec sleep infinity'],
            'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'},
                          'limits': {'cpu': '100m', 'memory': '64Mi'}},
            'securityContext': {'runAsNonRoot': False, 'runAsUser': 0, 'runAsGroup': 0,
                                'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                'capabilities': {'drop': ['ALL'], 'add': ['NET_ADMIN']}},
            'readinessProbe': {'exec': {'command': ['sh', '-ec',
                'tc qdisc show dev eth0 | grep -q "qdisc tbf 1: root"']}, 'periodSeconds': 5}
        })
    return spec, routing, images, docs


def require_subset(expected, actual, location='spec'):
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            raise RuntimeError('Live configuration differs: ' + location)
        for key, value in expected.items():
            if key not in actual:
                raise RuntimeError('Missing live field: ' + location + '.' + key)
            require_subset(value, actual[key], location + '.' + key)
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            raise RuntimeError('Live list differs: ' + location)
        for index, (value, item) in enumerate(zip(expected, actual)):
            require_subset(value, item, f'{location}[{index}]')
    elif expected != actual:
        raise RuntimeError('Live configuration differs: ' + location)


def tc_read(out, label, pod):
    data = m1.kube('-n', m1.NS, 'exec', pod, '-c', 'tc', '--',
                   'tc', '-j', '-s', '-d', 'qdisc', 'show', 'dev', 'eth0').stdout
    m1.save(out, label + '.json', data)
    m1.save(out, label + '.txt', m1.kube('-n', m1.NS, 'exec', pod, '-c', 'tc', '--',
            'tc', '-s', '-d', 'qdisc', 'show', 'dev', 'eth0').stdout)
    return json.loads(data)


def tbf(data, rate):
    roots = [q for q in data if q.get('root')]
    if len(roots) != 1 or roots[0]['kind'] != 'tbf' or roots[0]['handle'] != '1:':
        raise RuntimeError('Expected a single root TBF 1:')
    q = roots[0]
    if q['options']['rate'] != rate * 1000000 / 8:
        raise RuntimeError('Live TBF rate differs from the experiment')
    return q


def verify_live(out, state, spec, routing, images, docs):
    node, target = m1.target_node()
    envoy_name, envoy = m1.one_pod(state, m1.NS, routing['gatewayDeployment'])
    m1.verify_image_lock(envoy, images['images']['envoy']['image'])
    backends = {}
    for route, doc in zip(routing['classes'], docs):
        name, pod = m1.one_pod(state, m1.NS, route['backendDeployment'])
        if {c['name'] for c in pod['containers']} != {'iperf3', 'tc'}:
            raise RuntimeError('Both backends require the same two containers')
        for container in pod['containers']:
            m1.verify_image_lock({'containers': [container]}, images['images']['backend']['image'])
        live = json.loads(m1.kube('-n', m1.NS, 'get', 'deployment', route['backendDeployment'], '-o', 'json').stdout)
        require_subset(doc['spec'], live['spec'])
        actual_pod = json.loads(m1.kube('-n', m1.NS, 'get', 'pod', name, '-o', 'json').stdout)
        require_subset(doc['spec']['template']['spec'], actual_pod['spec'])
        if actual_pod['spec'].get('hostNetwork') or actual_pod['spec'].get('initContainers'):
            raise RuntimeError('Unexpected host networking or init containers')
        for container in actual_pod['spec']['containers']:
            if container['securityContext'].get('privileged'):
                raise RuntimeError('Privileged containers are prohibited')
        m1.save(out, route['name'] + '-deployment.json', live)
        tbf(tc_read(out, route['name'] + '-qdisc', name), spec['ratesMbps'][route['name']])
        backends[route['name']] = (name, pod)
    if any(p['node'] != node['metadata']['name'] for _, p in [(envoy_name, envoy), *backends.values()]):
        raise RuntimeError('M3 requires both backends and Envoy on the control-role node')
    expected = m2.render()[2]
    cm = json.loads(m1.kube('-n', m1.NS, 'get', 'configmap', routing['configMap'], '-o', 'json').stdout)
    if cm['data'] != expected[0]['data']:
        raise RuntimeError('Inherited Envoy configuration changed')
    live_envoy = json.loads(m1.kube('-n', m1.NS, 'get', 'deployment', routing['gatewayDeployment'], '-o', 'json').stdout)
    require_subset(next(d['spec'] for d in expected if d['kind'] == 'Deployment' and
                        d['metadata']['name'] == routing['gatewayDeployment']), live_envoy['spec'])
    services = {s['metadata']['name']: s for s in json.loads(m1.kube('-n', m1.NS, 'get', 'services', '-o', 'json').stdout)['items']}
    for doc in [d for d in expected if d['kind'] == 'Service'] + [
            d for d in m1.config()[0] if d['kind'] == 'Service' and d['metadata']['name'] == 'cnf-m1-backend']:
        require_subset(doc['spec'], services[doc['metadata']['name']]['spec'])
    for route in routing['classes']:
        endpoint = json.loads(m1.kube('-n', m1.NS, 'get', 'endpoints', route['backendService'], '-o', 'json').stdout)
        addresses = [a['ip'] for s in endpoint.get('subsets', []) for a in s.get('addresses', [])]
        if addresses != [backends[route['name']][1]['ip']]:
            raise RuntimeError('Backend endpoint does not match the single verified Pod')
    m1.save(out, 'services.json', services)
    return target, envoy_name, envoy, backends, services


def validate_downlink(data, logs, correct, wrong, route, routing, service_ip, envoy_ip, started, ended, duration):
    start, end = data.get('start', {}), data.get('end', {})
    received, sent = end.get('sum_received', {}), end.get('sum_sent', {})
    if (data.get('error') or start.get('test_start', {}).get('reverse') != 1 or
            received.get('bytes', 0) <= 0 or sent.get('bytes', 0) < received.get('bytes', 0) or
            received.get('seconds', 0) < duration * .95 or received.get('bits_per_second', 0) <= 0):
        raise RuntimeError('Incomplete or invalid TCP downlink receiver measurement')
    # Reuse independent M2 route/cookie checks, substituting the downlink byte direction.
    # Sender may have in-flight buffered bytes at stop; retain both raw summaries.
    adapted = copy.deepcopy(data)
    adapted['end']['sum_sent']['bytes'] = received['bytes']
    entries = []
    for line in logs.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            item['bytes_received'] = item.get('bytes_sent')
            entries.append(json.dumps(item))
    result = m2.validate_round(adapted, '\n'.join(entries), correct, wrong, route, routing,
                               service_ip, envoy_ip, started, ended)
    result.update(receiverMbps=received['bits_per_second'] / 1e6,
                  senderMbps=sent['bits_per_second'] / 1e6, senderBytes=sent['bytes'],
                  receiverWindowSeconds=received['seconds'], senderWindowSeconds=sent['seconds'])
    return result


def qdisc_delta(before, after, rate, received):
    a, b = tbf(before, rate), tbf(after, rate)
    delta = {k: b[k] - a[k] for k in ['bytes', 'packets', 'drops', 'overlimits', 'requeues']}
    if delta['bytes'] < received or delta['packets'] <= 0 or delta['overlimits'] <= 0:
        raise RuntimeError('Missing transmitted payload or active TBF shaping counters')
    if any(value < 0 for value in delta.values()):
        raise RuntimeError('qdisc counters reset during measurement')
    return {**delta, 'backlogBytesAfter': b['backlog'], 'backlogPacketsAfter': b['qlen']}


def summarize(rows, spec, count):
    classes = {}
    for name, cap in spec['ratesMbps'].items():
        group = [r for r in rows if r['businessClass'] == name]
        if len(group) != count or any(r['outcome'] != 'passed' for r in group):
            raise RuntimeError('Incomplete M3 batch')
        median = statistics.median(r['receiverMbps'] for r in group)
        classes[name] = {'rounds': count, 'capMbps': cap, 'medianReceiverMbps': median,
                         'receiverMbps': [r['receiverMbps'] for r in group]}
        if median > cap * (1 + spec['maximumMedianExcessFraction']):
            raise RuntimeError('Median exceeds the configured cap by more than 15%')
    ratio = classes['edge']['medianReceiverMbps'] / classes['core']['medianReceiverMbps']
    if ratio < spec['minimumMedianRatio']:
        raise RuntimeError('Median ratio is below the agreed threshold')
    return {'classes': classes, 'medianRatio': ratio, 'sessions': len(rows),
            'proxyConnections': sum(r['proxyConnections'] for r in rows),
            'fullAcceptance': count == spec['roundsPerClass']}


def transfer(out, ue, upf, state, command, duration):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + duration + 20
    samples, stdout, stderr = [], '', ''
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=1)
                break
            except subprocess.TimeoutExpired:
                current = m1.snapshot()
                m1.save(out, 'guard-pods.json', current)
                m1.assert_steady(state, current)
                memory = int(m1.kube('-n', m1.CORE, 'exec', upf, '--', 'cat',
                                     '/sys/fs/cgroup/memory.current', timeout=5).stdout)
                samples.append({'time': m1.now(), 'upfMemoryBytes': memory})
                m1.save(out, 'resource-samples.json', samples)
                if memory >= 512 * 1024 ** 2 or time.monotonic() >= deadline:
                    raise RuntimeError('UPF memory threshold or session deadline exceeded')
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
            m1.kube('-n', m1.CORE, 'exec', ue, '--', 'pkill', '-TERM', '-x', 'iperf3', required=False)
        m1.save(out, 'resource-samples.json', samples)
        m1.save(out, 'iperf.json', stdout)
        m1.save(out, 'iperf.stderr.txt', stderr)
    if process.returncode:
        raise RuntimeError('iperf failed; see retained raw output')
    return json.loads(stdout)


def measure(out, spec, routing, images, docs, smoke=False, unshaped=False):
    state = m1.baseline(out, 'before-measure')
    if unshaped:
        target, envoy_name, envoy, backends, services = m2.verify_live(state, routing, images, m2.render()[2])
        m1.save(out, 'services.json', services)
    else:
        target, envoy_name, envoy, backends, services = verify_live(out, state, spec, routing, images, docs)
    ue, source = m1.ue_path(out, state, target)
    upf, _ = m1.one_pod(state, m1.CORE, 'free5gc-helm-free5gc-upf-upf')
    if m1.kube('-n', m1.CORE, 'exec', ue, '--', 'pgrep', '-x', 'iperf3', required=False).returncode != 1:
        raise RuntimeError('UE already runs iperf or process inspection failed')
    m1.save(out, 'iperf-version.txt', m1.kube('-n', m1.CORE, 'exec', ue, '--', 'iperf3', '--version').stdout)
    count, duration, offered = (1, 5, 5) if unshaped else (1 if smoke else spec['roundsPerClass'], spec['durationSeconds'], spec['offeredMbps'])
    rows = []
    try:
        for number in range(1, count + 1):
            for route in routing['classes']:
                folder = out / f'{number:02d}-{route["name"]}'
                folder.mkdir()
                row = {'round': number, 'businessClass': route['name'], 'outcome': 'failed'}
                try:
                    m1.assert_steady(state, m1.snapshot())
                    name = backends[route['name']][0]
                    if not unshaped:
                        first_tc = tc_read(folder, 'qdisc-before', name)
                    first = m1.upf_observation(folder, 'before', upf)
                    started = m1.now()
                    row['startedAt'] = started
                    command = ['kubectl', '-n', m1.CORE, 'exec', ue, '--', 'iperf3', '-c', target,
                        '-B', source, '-p', str(route['nodePort']), '-R', '-P', '1', '-b', str(offered) + 'M',
                        '-t', str(duration), '-l', str(spec['blockBytes']), '-J', '--get-server-output', '--connect-timeout', '5000']
                    m1.save(folder, 'command.json', command)
                    data = transfer(folder, ue, upf, state, command, duration)
                    ended = m1.now()
                    row['trafficEndedAt'] = ended
                    last = m1.upf_observation(folder, 'after', upf)
                    delta = [b - a for a, b in zip(m1.path_counters(first['interfaces']), m1.path_counters(last['interfaces']))]
                    if not all(x > 0 for x in delta):
                        raise RuntimeError('UPF did not record bidirectional traffic')
                    row.update(upfGtpRxBytesDelta=delta[0], upfGtpTxBytesDelta=delta[1])
                    if not unshaped:
                        last_tc = tc_read(folder, 'qdisc-after', name)
                        row['qdiscDelta'] = qdisc_delta(first_tc, last_tc, spec['ratesMbps'][route['name']],
                                                      data['end']['sum_received']['bytes'])
                    other = next(r for r in routing['classes'] if r['name'] != route['name'])
                    wait_started = time.monotonic()
                    while True:
                        logs = m1.kube('-n', m1.NS, 'logs', envoy_name, '--since-time=' + started).stdout
                        correct = m1.kube('-n', m1.NS, 'logs', name, '-c', 'iperf3', '--since-time=' + started).stdout
                        wrong = m1.kube('-n', m1.NS, 'logs', backends[other['name']][0], '-c', 'iperf3', '--since-time=' + started).stdout
                        for file, value in [('envoy-access.txt', logs), ('expected-backend.txt', correct), ('other-backend.txt', wrong)]:
                            m1.save(folder, file, value)
                        try:
                            evidence = validate_downlink(data, logs, correct, wrong, route, routing,
                                services[route['backendService']]['spec']['clusterIP'], envoy['ip'], started, ended, duration)
                            break
                        except m1.PendingProxyLogs:
                            if time.monotonic() - wait_started >= 15:
                                raise
                            m1.assert_steady(state, m1.snapshot())
                            time.sleep(.25)
                    m1.assert_steady(state, m1.snapshot())
                    row.update(evidence, outcome='passed', logWaitSeconds=round(time.monotonic() - wait_started, 3))
                    print(f'{number:02d}/{count:02d} {route["name"]}: {row["receiverMbps"]:.3f} Mbps, passed', flush=True)
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
    return {'sessions': len(rows), 'fullAcceptance': False} if unshaped else summarize(rows, spec, count)


def deploy(out, spec, routing, images, docs):
    before = m1.baseline(out, 'before-deploy')
    m2.verify_live(before, routing, images, m2.render()[2])
    m1.save(out, 'server-dry-run.txt', m1.kube('apply', '--dry-run=server', '-f', out / 'manifest.yaml').stdout)
    m1.save(out, 'apply.txt', m1.kube('apply', '-f', out / 'manifest.yaml').stdout)
    for route in routing['classes']:
        m1.save(out, route['name'] + '-rollout.txt', m1.kube('-n', m1.NS, 'rollout', 'status',
            'deployment/' + route['backendDeployment'], '--timeout=120s', timeout=130).stdout)
    after = m1.baseline(out, 'after-deploy')
    m1.assert_steady({m1.CORE: before[m1.CORE]}, {m1.CORE: after[m1.CORE]})
    envoy, _ = m1.one_pod(before, m1.NS, routing['gatewayDeployment'])
    m1.assert_steady({m1.NS: {envoy: before[m1.NS][envoy]}}, {m1.NS: {envoy: after[m1.NS][envoy]}})
    verify_live(out, after, spec, routing, images, docs)
    # No traffic: demonstrate explicit qdisc removal and exact reapplication within each Pod only.
    for route in routing['classes']:
        pod, _ = m1.one_pod(after, m1.NS, route['backendDeployment'])
        prefix = ['-n', m1.NS, 'exec', pod, '-c', 'tc', '--']
        m1.kube(*prefix, 'tc', 'qdisc', 'del', 'dev', 'eth0', 'root')
        removed = tc_read(out, route['name'] + '-qdisc-removed', pod)
        if any(q['kind'] == 'tbf' for q in removed):
            raise RuntimeError('TBF removal did not take effect')
        m1.kube(*prefix, *tc_command(spec, route['name']))
        tbf(tc_read(out, route['name'] + '-qdisc-reapplied', pod), spec['ratesMbps'][route['name']])
    # A brief readiness transition during the no-traffic removal test is expected.
    for route in routing['classes']:
        m1.kube('-n', m1.NS, 'rollout', 'status', 'deployment/' + route['backendDeployment'], '--timeout=30s')
    m1.assert_steady(after, m1.snapshot())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['render', 'preflight', 'deploy', 'measure'])
    parser.add_argument('--out', required=True)
    parser.add_argument('--smoke', action='store_true', help='One round per class, never full acceptance')
    args = parser.parse_args()
    spec, routing, images, docs = render()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    inputs = [SPEC, m2.SPEC, m1.MANIFEST, m1.LOCK, ROOT / 'scripts/cnf-m1.py', ROOT / 'scripts/cnf-m2.py',
              Path(__file__), ROOT / 'scripts/free5gc-profile.py', ROOT / 'infra/profiles/free5gc-3node-no-multus-v1.json']
    m1.save(out, 'metadata.json', {**{k: spec[k] for k in ['route', 'stage', 'milestone', 'configVersion']},
        'startedAt': m1.now(), 'command': args.command, 'smoke': args.smoke, 'evidencePath': str(out),
        'sha256LF': {str(p.relative_to(ROOT)): m2.sha(p) for p in inputs}})
    m1.save(out, 'experiment.json', spec)
    m1.save(out, 'routing.json', routing)
    m1.save(out, 'images.json', images)
    m1.save(out, 'manifest.yaml', yaml.safe_dump_all(docs, sort_keys=False))
    result = {'outcome': 'failed', 'command': args.command}
    try:
        if args.command == 'deploy':
            deploy(out, spec, routing, images, docs)
        elif args.command in ['measure', 'preflight']:
            result['evidence'] = measure(out, spec, routing, images, docs, args.smoke, args.command == 'preflight')
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
        print(f'CNF M3 ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
