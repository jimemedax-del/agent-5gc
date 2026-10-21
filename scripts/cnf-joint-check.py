#!/usr/bin/env python3
"""Fixed cross-node Envoy/NetworkPolicy/TBF joint check; restore protected state."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
import uuid

import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / 'infra/cnfs/joint-check-experiment.json'
loader = importlib.util.spec_from_file_location('joint_live', ROOT / 'scripts/cnf-planner-live.py')
live = importlib.util.module_from_spec(loader)
loader.loader.exec_module(live)
base, c, c2 = live.c.a.base, live.c, live.c2
m1, m2, m3, m4, np = base.m1, base.m2, base.m3, base.m4, base.np
BACKENDS = ['cnf-m1-backend', 'cnf-m2-edge']


def require(value, message):
    if not value:
        raise RuntimeError(message)


def config():
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    require(spec['configVersion'] == 'cnf-joint-check-v1' and spec['initialAndFinalState'] == 'protected',
            'Unknown joint check/state')
    require(spec['phases'] == [{'name': 'A-unshaped', 'roundsPerClass': 3, 'shaped': False},
                              {'name': 'B-shaped', 'roundsPerClass': 5, 'shaped': True},
                              {'name': 'C-unshaped', 'roundsPerClass': 3, 'shaped': False}], 'Unapproved phases')
    require((spec['direction'], spec['protocol'], spec['durationSeconds'], spec['offeredMbps'], spec['blockBytes']) ==
            ('downlink', 'TCP', 15, 20, 16384), 'Unapproved workload')
    inherited = m3.render()[0]
    require(spec['ratesMbps'] == inherited['ratesMbps'] and spec['qdisc'] == inherited['qdisc'], 'Queue changed')
    require(spec['acceptance'] == {'unshapedMinimumMedianMbps': 18, 'shapedMinimumCapFraction': .8,
            'shapedMaximumCapFraction': 1.15, 'minimumShapedMedianRatio': 2,
            'minimumReceiverDurationFraction': .95, 'maximumUpfMemoryBytes': 536870912}, 'Acceptance changed')
    return spec


def render():
    docs = copy.deepcopy(base.render('protected'))
    helper = copy.deepcopy(m3.render()[3][0]['spec']['template']['spec']['containers'][1])
    helper['command'] = ['sleep', 'infinity']
    helper['readinessProbe'] = {'exec': {'command': ['sh', '-ec', 'kill -0 1']}, 'periodSeconds': 5}
    for doc in docs:
        if doc['kind'] == 'Deployment' and doc['metadata']['name'] in BACKENDS:
            doc['spec']['template']['metadata'].setdefault('annotations', {})['cnf.lab/joint-version'] = config()['configVersion']
            doc['spec']['template']['spec']['containers'].append(copy.deepcopy(helper))
    return docs


def unaffected(state):
    result = copy.deepcopy(state)
    result[m1.NS] = {name: p for name, p in result[m1.NS].items()
                      if not any(name.startswith(prefix + '-') for prefix in BACKENDS)}
    return result


def wait_rollouts(out, label):
    for name in BACKENDS:
        m1.save(out, label + '-' + name + '.txt', m1.kube('-n', m1.NS, 'rollout', 'status',
                'deployment/' + name, '--timeout=120s', timeout=130).stdout)
    deadline = time.monotonic() + 60
    while True:
        items = json.loads(m1.kube('-n', m1.NS, 'get', 'pods', '-o', 'json').stdout)['items']
        if not any(p['metadata'].get('deletionTimestamp') for p in items):
            break
        require(time.monotonic() < deadline, 'Old backend Pod did not terminate')
        time.sleep(1)


def qdisc(out, label, pod, shaped, name):
    data = m3.tc_read(out, label, pod)
    if shaped:
        q = m3.tbf(data, config()['ratesMbps'][name])
        require(len(data) == 1 and q['options']['burst'] == '32Kb/1' and
                q['options']['lat'] == 100000, 'Unexpected queue/burst/latency')
    else:
        m4.no_shaping(data)
    return data


def verify_joint(out, shaped, state=None):
    out.mkdir()
    state = state or c2.snapshot()
    m1.assert_steady(state, state)
    objects = base.inventory()
    expected = {(d['kind'], d['metadata']['name']): d for d in render()}
    require(set(objects) == set(expected), 'Joint resource inventory changed')
    for key, wanted in expected.items():
        actual = objects[key]
        field = 'data' if key[0] == 'ConfigMap' else 'spec'
        m3.require_subset(wanted[field], actual[field])
        if key[0] in ['ConfigMap', 'NetworkPolicy']:
            require(wanted[field] == actual[field], 'Config/policy changed')
        if key[0] == 'Deployment':
            pod_spec = actual['spec']['template']['spec']
            require(not any(pod_spec.get(k) for k in ['hostNetwork', 'hostPID', 'hostIPC', 'initContainers']),
                    'Unexpected host namespaces/init containers')
            for got, want in zip(pod_spec['containers'], wanted['spec']['template']['spec']['containers']):
                require(got['securityContext'] == want['securityContext'] and not got.get('env') and not got.get('envFrom'),
                        'Unexpected container privileges/environment')
    m1.save(out, 'resources.json', [{'kind': k[0], 'name': k[1],
            'body': o.get('data') if k[0] == 'ConfigMap' else o['spec']} for k, o in sorted(objects.items())])
    placement, routing, images, _ = m4.render(True)
    edge = m4.node_check(out, placement, images['images']['backend']['image'])
    control, target = m1.target_node()
    envoy_name, envoy = m1.one_pod(state, m1.NS, routing['gatewayDeployment'])
    require(envoy['node'] == control['metadata']['name'], 'Envoy placement changed')
    m1.verify_image_lock(envoy, images['images']['envoy']['image'])
    services = {name: obj for (kind, name), obj in objects.items() if kind == 'Service'}
    backends = {}
    for route in routing['classes']:
        name, pod = m1.one_pod(state, m1.NS, route['backendDeployment'])
        expected_node = edge['metadata']['name'] if route['name'] == 'edge' else control['metadata']['name']
        require(pod['node'] == expected_node and {x['name'] for x in pod['containers']} == {'iperf3', 'tc'},
                'Wrong backend placement/containers')
        for container in pod['containers']:
            m1.verify_image_lock({'containers': [container]}, images['images']['backend']['image'])
        actual = json.loads(m1.kube('-n', m1.NS, 'get', 'pod', name, '-o', 'json').stdout)['spec']
        desired = expected['Deployment', route['backendDeployment']]['spec']['template']['spec']
        m3.require_subset(desired, actual)
        require(not any(actual.get(k) for k in ['hostNetwork', 'hostPID', 'hostIPC', 'initContainers']), 'Unsafe live Pod')
        for got, want in zip(actual['containers'], desired['containers']):
            require(got['securityContext'] == want['securityContext'], 'Live Pod privileges differ')
        endpoint = json.loads(m1.kube('-n', m1.NS, 'get', 'endpoints', route['backendService'], '-o', 'json').stdout)
        slices = json.loads(m1.kube('-n', m1.NS, 'get', 'endpointslices', '-l',
                           'kubernetes.io/service-name=' + route['backendService'], '-o', 'json').stdout)['items']
        m4.validate_endpoint(endpoint, slices, name, pod, expected_node)
        m1.save(out, route['name'] + '-endpoints.json', endpoint)
        m1.save(out, route['name'] + '-endpointslices.json', slices)
        qdisc(out, route['name'] + '-qdisc', name, shaped, route['name'])
        backends[route['name']] = (name, pod)
    m1.save(out, 'pods.json', state)
    m1.save(out, 'qos-switch.txt', c.native_switch())
    m1.save(out, 'services.json', services)
    return target, envoy_name, envoy, backends, services


def steady(state, locked, out, label):
    m1.assert_steady(state, c2.snapshot())
    require(np.runtime() == locked, 'CNF runtime changed during formal measurement')
    np.policy_state(out, label + '-policies', np.render()[5], True)
    c.native_switch()


def transfer(folder, command, state, token, ue, upf):
    job = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    samples, stdout, stderr = [], '', ''
    deadline = time.monotonic() + config()['durationSeconds'] + 20
    try:
        while True:
            try:
                stdout, stderr = job.communicate(timeout=1)
                break
            except subprocess.TimeoutExpired:
                current = c2.snapshot()
                m1.save(folder, 'guard-pods.json', current)
                m1.assert_steady(state, current)
                memory = int(m1.kube('-n', m1.CORE, 'exec', upf, '--', 'cat',
                                    '/sys/fs/cgroup/memory.current', timeout=5).stdout)
                samples.append({'time': m1.now(), 'upfMemoryBytes': memory})
                m1.save(folder, 'resource-samples.json', samples)
                require(0 < memory < config()['acceptance']['maximumUpfMemoryBytes'], 'UPF memory guard exceeded')
                require(time.monotonic() < deadline, 'Traffic deadline exceeded')
        require(job.returncode == 0, 'iperf failed; see raw output')
        return json.loads(stdout)
    finally:
        m1.save(folder, 'iperf.json', stdout)
        m1.save(folder, 'iperf.stderr.txt', stderr)
        m1.save(folder, 'resource-samples.json', samples)
        try:
            stopped = live.stop_owned_process(ue, command, token)
            m1.save(folder, 'cleanup.json', {'returncode': stopped.returncode, 'stderr': stopped.stderr})
            require(stopped.returncode in [0, 1], 'Owned process cleanup failed')
        finally:
            if job.poll() is None:
                job.terminate()
                try:
                    job.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    job.kill()
                    job.wait(timeout=5)


def summarize(rows, phase, spec):
    require(len(rows) == 2 * phase['roundsPerClass'] and all(r['outcome'] == 'passed' for r in rows), 'Incomplete phase')
    medians = {}
    for name, cap in spec['ratesMbps'].items():
        group = [r['receiverMbps'] for r in rows if r['businessClass'] == name]
        require(len(group) == phase['roundsPerClass'], 'Missing class rounds')
        value = medians[name] = statistics.median(group)
        if phase['shaped']:
            require(cap * spec['acceptance']['shapedMinimumCapFraction'] <= value <=
                    cap * spec['acceptance']['shapedMaximumCapFraction'], 'Shaped median outside frozen bounds')
        else:
            require(value >= spec['acceptance']['unshapedMinimumMedianMbps'], 'Unshaped capacity/recovery below 18 Mbps')
    ratio = medians['edge'] / medians['core']
    if phase['shaped']:
        require(ratio >= spec['acceptance']['minimumShapedMedianRatio'], 'Shaped ratio below 2')
    return {'medianReceiverMbps': medians, 'medianRatio': ratio, 'sessions': len(rows)}


def measure_phase(out, phase, state, locked):
    out.mkdir()
    spec, routing = config(), m2.render()[0]
    topology = verify_joint(out / 'before', phase['shaped'], state)
    target, envoy_name, envoy, backends, services = topology
    ue, source = m1.ue_path(out, state, target)
    upf, _ = m1.one_pod(state, m1.CORE, 'free5gc-helm-free5gc-upf-upf')
    require(m1.kube('-n', m1.CORE, 'exec', ue, '--', 'pgrep', '-x', 'iperf3', required=False).returncode == 1,
            'Unmanaged iperf process present')
    rows = []
    for number in range(1, phase['roundsPerClass'] + 1):
        for route in routing['classes']:
            folder = out / f'{number:02d}-{route["name"]}'
            folder.mkdir()
            row = {'round': number, 'businessClass': route['name'], 'phase': phase['name'], 'outcome': 'failed'}
            try:
                steady(state, locked, folder, 'before')
                pod = backends[route['name']][0]
                before_tc = qdisc(folder, 'qdisc-before', pod, phase['shaped'], route['name'])
                first = m1.upf_observation(folder, 'before', upf)
                row['startedAt'] = m1.now()
                token = uuid.uuid4().hex[:24]
                command = ['kubectl', '-n', m1.CORE, 'exec', ue, '--', 'iperf3', '-c', target, '-B', source,
                           '-p', str(route['nodePort']), '-R', '-P', '1', '-b', '20M', '-t', '15', '-l', '16384',
                           '-J', '--get-server-output', '--connect-timeout', '5000', '--extra-data', token]
                m1.save(folder, 'command.json', command)
                data = transfer(folder, command, state, token, ue, upf)
                row['trafficEndedAt'] = m1.now()
                received = data['end']['sum_received']
                row['receiverMbps'] = received['bytes'] * 8 / received['seconds'] / 1e6
                last = m1.upf_observation(folder, 'after', upf)
                delta = [b - a for a, b in zip(m1.path_counters(first['interfaces']), m1.path_counters(last['interfaces']))]
                require(all(d > 0 for d in delta), 'No bidirectional UPF counters')
                row.update(upfGtpRxBytesDelta=delta[0], upfGtpTxBytesDelta=delta[1])
                after_tc = qdisc(folder, 'qdisc-after', pod, phase['shaped'], route['name'])
                if phase['shaped']:
                    row['qdiscDelta'] = m3.qdisc_delta(before_tc, after_tc, spec['ratesMbps'][route['name']], received['bytes'])
                other = next(r for r in routing['classes'] if r['name'] != route['name'])
                waiting = time.monotonic()
                while True:
                    logs = m1.kube('-n', m1.NS, 'logs', envoy_name, '--since-time=' + row['startedAt']).stdout
                    correct = m1.kube('-n', m1.NS, 'logs', pod, '-c', 'iperf3', '--since-time=' + row['startedAt']).stdout
                    wrong = m1.kube('-n', m1.NS, 'logs', backends[other['name']][0], '-c', 'iperf3', '--since-time=' + row['startedAt']).stdout
                    for file, value in [('envoy-access.txt', logs), ('expected-backend.txt', correct), ('other-backend.txt', wrong)]:
                        m1.save(folder, file, value)
                    try:
                        evidence = m3.validate_downlink(data, logs, correct, wrong, route, routing,
                            services[route['backendService']]['spec']['clusterIP'], envoy['ip'],
                            row['startedAt'], row['trafficEndedAt'], spec['durationSeconds'])
                        break
                    except m1.PendingProxyLogs:
                        require(time.monotonic() - waiting < 15, 'Proxy logs incomplete')
                        m1.assert_steady(state, c2.snapshot())
                        time.sleep(.25)
                steady(state, locked, folder, 'after')
                row.update(evidence, outcome='passed')
                row['receiverMbps'] = received['bytes'] * 8 / received['seconds'] / 1e6
                print(f'{phase["name"]} {number}/{phase["roundsPerClass"]} {route["name"]}: {row["receiverMbps"]:.6f} Mbps', flush=True)
            except Exception as error:
                row['error'] = str(error)
                raise
            finally:
                rows.append(row)
                m1.save(folder, 'summary.json', row)
                m1.save(out, 'rounds.json', rows)
    verify_joint(out / 'after', phase['shaped'], c2.snapshot())
    result = summarize(rows, phase, spec)
    m1.save(out, 'summary.json', {'outcome': 'passed', **result})
    return result


def set_queues(out, shaped, state):
    out.mkdir()
    verify_joint(out / 'before', not shaped, state)
    for route in m2.render()[0]['classes']:
        pod, _ = m1.one_pod(state, m1.NS, route['backendDeployment'])
        m1.assert_steady(state, c2.snapshot())
        args = m3.tc_command(config(), route['name']) if shaped else ['tc', 'qdisc', 'del', 'dev', 'eth0', 'root']
        m1.save(out, route['name'] + '-command.json', args)
        m1.save(out, route['name'] + '-result.txt', m1.kube('-n', m1.NS, 'exec', pod, '-c', 'tc', '--', *args).stdout)
    verify_joint(out / 'after', shaped, c2.snapshot())


def restore(out, original, joint, before):
    out.mkdir()
    objects = base.inventory()
    # Only known original/joint backend specs may be restored, including partial apply.
    for original_doc in original:
        name = original_doc['metadata']['name']
        actual = objects['Deployment', name]
        candidates = [original_doc, next(d for d in joint if d['metadata']['name'] == name)]
        matched = False
        for candidate in candidates:
            try:
                m3.require_subset(candidate['spec'], actual['spec'])
                matched = True
                break
            except RuntimeError:
                pass
        require(matched, 'Foreign backend config; refuse overwrite during restore: ' + name)
    manifest = out / 'restore.yaml'
    m1.save(out, manifest.name, yaml.safe_dump_all(original, sort_keys=False))
    m1.save(out, 'dry-run.txt', m1.kube('apply', '--dry-run=server', '-f', manifest).stdout)
    m1.save(out, 'apply.txt', m1.kube('apply', '-f', manifest).stdout)
    wait_rollouts(out, 'restore')
    base.verify(out / 'protected', 'protected')
    m1.baseline(out, 'strict-final')
    final = c2.snapshot()
    m1.assert_steady(unaffected(before), unaffected(final))
    m1.save(out, 'final-pods.json', final)
    m1.save(out, 'summary.json', {'outcome': 'passed', 'state': 'protected', 'backendReplacementExpected': True})


def run(out):
    spec = config()
    hashes = {}
    sources = [p for directory in ['scripts', 'infra', 'tests'] for p in (ROOT / directory).rglob('*')
               if p.is_file() and '__pycache__' not in p.parts and p.suffix in ['.py', '.sh', '.json', '.yaml', '.yml']]
    sources += [ROOT / name for name in ['results/20261020-cnf-contention/metadata.json',
                                        'results/20261020-cnf-contention-uplink/metadata.json']]
    for source in sorted(sources):
        relative = source.relative_to(ROOT)
        target = out / 'inputs' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        hashes[relative.as_posix()] = live.p.file_hash(source)
    m1.save(out, 'input-hashes.json', hashes)
    m1.save(out, 'experiment.json', spec)
    m1.save(out, 'routing.json', m2.render()[0])
    joint = [d for d in render() if d['kind'] == 'Deployment' and d['metadata']['name'] in BACKENDS]
    original = [d for d in base.render('protected') if d['kind'] == 'Deployment' and d['metadata']['name'] in BACKENDS]
    manifest = out / 'joint-backends.yaml'
    m1.save(out, manifest.name, yaml.safe_dump_all(joint, sort_keys=False))
    m1.save(out, 'restore-backends.yaml', yaml.safe_dump_all(original, sort_keys=False))
    result = {'outcome': 'failed', 'startedAt': m1.now(), 'phases': {}, 'restoration': 'not_needed'}
    changed = False
    try:
        with live.Backend() as lock:
            initial = lock.full(out / 'initial')
            require(initial['queueMode'] == 'none', 'C1 must remain unshaped')
            before = initial['pods']
            base.verify(out / 'initial-protected', 'protected')
            m1.save(out, 'dry-run.txt', m1.kube('apply', '--dry-run=server', '-f', manifest).stdout)
            try:
                changed = True
                m1.save(out, 'apply.txt', m1.kube('apply', '-f', manifest).stdout)
                wait_rollouts(out, 'prepare')
                state = c2.snapshot()
                m1.assert_steady(unaffected(before), unaffected(state))
                m1.save(out, 'formal-pods.json', state)
                verify_joint(out / 'formal-ready', False, state)
                locked = np.runtime()
                m1.save(out, 'runtime.json', locked)
                for phase in spec['phases']:
                    if phase['name'] != 'A-unshaped':
                        set_queues(out / ('transition-' + phase['name']), phase['shaped'], state)
                    print('Starting ' + phase['name'], flush=True)
                    result['phases'][phase['name']] = measure_phase(out / phase['name'], phase, state, locked)
                result['outcome'] = 'passed'
            finally:
                if changed:
                    result['restoration'] = 'failed'
                    restore(out / 'restoration', original, joint, before)
                    result['restoration'] = 'passed'
                final = lock.full(out / 'final')
                m1.assert_steady(unaffected(initial['pods']), unaffected(final['pods']))
                # All non-pod resources, session bindings and unrelated queues must match exactly.
                for field in ['resources', 'bindings', 'queueMode', 'queueShape', 'uplinkClean', 'nativeQos', 'endpoint']:
                    require(initial[field] == final[field], 'Final baseline differs: ' + field)
                result['strictFinal'] = 'passed'
    except Exception as error:
        result.update(outcome='failed', error=str(error))
        raise
    finally:
        result['endedAt'] = m1.now()
        m1.save(out, 'summary.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['render', 'run'])
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    if args.command == 'render':
        m1.save(args.out, 'manifest.yaml', yaml.safe_dump_all(render(), sort_keys=False))
        m1.save(args.out, 'experiment.json', config())
    else:
        print(json.dumps(run(args.out), ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
