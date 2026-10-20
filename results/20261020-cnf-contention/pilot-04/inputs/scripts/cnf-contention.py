#!/usr/bin/env python3
"""R2.S4/C1: container-local shared bottleneck, two UE receivers, fixed policies."""
import argparse
import copy
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / 'infra/cnfs/contention-experiment.json'
loader = importlib.util.spec_from_file_location('multi_ue', ROOT / 'scripts/cnf-multi-ue.py')
a = importlib.util.module_from_spec(loader)
loader.loader.exec_module(a)
m1, require = a.m1, a.require
NS, NAME, VERSION = 'cnf-contention', 'cnf-c-endpoint', 'cnf-contention-v1'


def config():
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    require((spec['configVersion'], spec['namespace'], spec['deployment'], spec['task']) ==
            (VERSION, NS, NAME, 'C1'), 'Unknown contention configuration')
    require((spec['capacityMbps'], spec['admissionMarginMbps'], spec['offeredMbpsPerUE'],
             spec['durationSeconds'], spec['steadyStartSeconds'], spec['steadyEndSeconds'],
             spec['rounds'], spec['datagramBytes'], spec['minimumOverlapSeconds']) ==
            (10, .5, 8, 30, 5, 25, 3, 1200, 28), 'Unapproved experiment load/window')
    require(spec['modes'] == ['shared', 'equal', 'guarantee-ue1', 'guarantee-ue2', 'priority-ue1', 'priority-ue2'],
            'Unexpected policy comparisons')
    require(spec['users'] == [
        {'name': 'ue1', 'supi': 'imsi-208930000000001', 'port': 5201, 'nodePort': 31401, 'pingTos': 40},
        {'name': 'ue2', 'supi': 'imsi-208930000000002', 'port': 5202, 'nodePort': 31402, 'pingTos': 80}], 'User endpoint drift')
    require((spec['direction'], spec['protocol'], spec['burstBytes'], spec['quantumBytes'],
             spec['fqCodelLimitPackets'], spec['maximumUpfMemoryBytes']) ==
            ('downlink', 'UDP', 32768, 1514, 256, 536870912), 'Unapproved queue/guard settings')
    require(spec['acceptance'] == {
        'unshapedMinimumMbpsPerUE': 7, 'congestedAggregateMinimumMbps': 8.5,
        'congestedAggregateMaximumMbps': 10.2, 'equalMinimumMbpsPerUE': 4,
        'guaranteedHighMinimumMbps': 5.5, 'guaranteedLowMinimumMbps': 1.7,
        'guaranteedHighMaximumMbps': 8.4, 'guaranteedLowMaximumMbps': 4.2,
        'maximumWindowViolationFraction': .1, 'priorityHighMinimumMbps': 6,
        'priorityLowMinimumMbps': 1.7, 'priorityMinimumHighLowRatio': 2}, 'Acceptance requires a new reviewed experiment version')
    return spec


def admit(requests):
    spec = config()
    require(set(requests) == {'ue1', 'ue2'}, 'Admission requires two known users')
    for value in requests.values():
        require(set(value) == {'rate', 'ceil', 'prio'} and all(
            type(value[k]) in [int, float] and math.isfinite(value[k]) for k in value), 'Invalid policy fields')
        require(0 < value['rate'] <= value['ceil'] <= spec['capacityMbps'] and
                type(value['prio']) is int and 0 <= value['prio'] <= 7, 'Invalid rate/priority')
    require(sum(v['rate'] for v in requests.values()) + spec['admissionMarginMbps'] <= spec['capacityMbps'],
            'Guaranteed rates exceed capacity including reserved margin')
    return copy.deepcopy(requests)


def policy(mode):
    require(mode in config()['modes'] + ['none'], 'Unknown fixed mode')
    if mode in ['none', 'shared']:
        return None
    if mode == 'equal':
        return admit({u: {'rate': 4, 'ceil': 8, 'prio': 0} for u in ['ue1', 'ue2']})
    high = mode.split('-')[1]
    return admit({u: {'rate': 6 if u == high and mode.startswith('guarantee') else 2,
                     'ceil': (8 if u == high else 4) if mode.startswith('guarantee') else 10,
                     'prio': 0 if u == high else 1} for u in ['ue1', 'ue2']})


def render():
    spec = config()
    image = m1.config()[1]['images']['backend']['image']
    labels = {'app': NAME, 'app.kubernetes.io/part-of': VERSION}
    base_security = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                     'capabilities': {'drop': ['ALL']}}
    containers = []
    for user in spec['users']:
        containers.append({'name': user['name'], 'image': image, 'imagePullPolicy': 'Never',
            'command': ['iperf3'], 'args': ['-s', '-p', str(user['port']), '--forceflush', '-V'],
            'securityContext': {**copy.deepcopy(base_security), 'runAsNonRoot': True, 'runAsUser': 65532, 'runAsGroup': 65532},
            'resources': {'requests': {'cpu': '100m', 'memory': '64Mi'}, 'limits': {'cpu': '500m', 'memory': '256Mi'}},
            'readinessProbe': {'exec': {'command': ['sh', '-ec', 'kill -0 1']}, 'periodSeconds': 2},
            'volumeMounts': [{'name': 'scratch', 'mountPath': '/tmp'}]})
    containers.append({'name': 'queue', 'image': image, 'imagePullPolicy': 'Never',
        'command': ['sh', '-ec', 'exec sleep infinity'],
        'securityContext': {**base_security, 'runAsNonRoot': False, 'runAsUser': 0,
                            'capabilities': {'drop': ['ALL'], 'add': ['NET_ADMIN']}},
        'resources': {'requests': {'cpu': '50m', 'memory': '32Mi'}, 'limits': {'cpu': '200m', 'memory': '64Mi'}},
        'readinessProbe': {'exec': {'command': ['sh', '-ec', 'kill -0 1']}, 'periodSeconds': 2}})
    return [
        {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NS, 'labels': {'app.kubernetes.io/part-of': VERSION}}},
        {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'namespace': NS, 'name': NAME, 'labels': labels},
         'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': NAME}}, 'template': {
            'metadata': {'labels': labels}, 'spec': {'automountServiceAccountToken': False,
                'nodeSelector': {'5gc.free5gc.org/plane': 'control'},
                'securityContext': {'fsGroup': 65532, 'seccompProfile': {'type': 'RuntimeDefault'}},
                'containers': containers, 'volumes': [{'name': 'scratch', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '16Mi'}}]}}}},
        {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'namespace': NS, 'name': NAME, 'labels': labels},
         'spec': {'type': 'NodePort', 'externalTrafficPolicy': 'Local', 'selector': {'app': NAME}, 'ports': [
            {'name': u['name'] + '-' + proto.lower(), 'port': u['port'], 'targetPort': u['port'],
             'nodePort': u['nodePort'], 'protocol': proto} for u in spec['users'] for proto in ['TCP', 'UDP']]}},
        {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
         'metadata': {'namespace': NS, 'name': NAME, 'labels': labels}, 'spec': {
            'podSelector': {'matchLabels': {'app': NAME}}, 'policyTypes': ['Ingress'], 'ingress': [
                {'ports': [{'protocol': p, 'port': u['port']} for u in spec['users'] for p in ['TCP', 'UDP']]},
                {'from': [{'ipBlock': {'cidr': c}} for c in ['192.168.244.129/32', '10.42.1.0/24', '10.60.0.0/16']]}]}}
    ]


def snapshot():
    state = a.snapshot()
    raw = json.loads(m1.kube('-n', NS, 'get', 'pods', '-o', 'json').stdout)['items']
    state[NS] = {}
    for p in raw:
        require(not p['metadata'].get('deletionTimestamp'), 'Contention Pod terminating')
        state[NS][p['metadata']['name']] = {'uid': p['metadata']['uid'], 'node': p['spec']['nodeName'],
            'ip': p['status'].get('podIP'), 'phase': p['status']['phase'], 'containers': [
                {key: c.get(key) for key in ['name', 'image', 'imageID', 'containerID', 'ready', 'restartCount', 'state']}
                for c in p['status'].get('containerStatuses', [])]}
    return state


def queue(*args):
    return m1.kube('-n', NS, 'exec', 'deployment/' + NAME, '-c', 'queue', '--', *args).stdout


def native_switch():
    value = m1.kube('-n', 'free5gc', 'exec', 'deployment/free5gc-helm-free5gc-upf-upf', '--',
                    'cat', '/proc/gtp5g/qos').stdout
    require(value.strip() == 'QoS Enable: 0', 'Native UPF policing drift')
    return value


def tc_state():
    raw = {'qdisc': queue('tc', '-j', '-s', 'qdisc', 'show', 'dev', 'eth0'),
           'class': queue('tc', '-s', '-d', 'class', 'show', 'dev', 'eth0'),
           'filter': queue('tc', '-s', 'filter', 'show', 'dev', 'eth0')}
    require(raw['qdisc'].strip(), 'Missing qdisc readback')
    module = importlib.util.spec_from_file_location('tc_decoder', ROOT / 'scripts/audit-cnf-contention.py')
    decoder = importlib.util.module_from_spec(module)
    module.loader.exec_module(decoder)
    result = decoder.parse_tc_native(json.loads(raw['qdisc']), raw['class'], raw['filter'])
    result['raw'] = raw
    return result


def tc_commands(mode):
    spec = config()
    selected = policy(mode)
    if mode == 'none':
        return []
    rows = ['qdisc add dev eth0 root handle 1: htb default ' + ('10' if mode == 'shared' else '99'),
            'class add dev eth0 parent 1: classid 1:1 htb rate 10mbit ceil 10mbit burst 32768 cburst 32768']
    classes = [('10', 10, 10, 0)] if mode == 'shared' else [
        ('10' if u == 'ue1' else '20', v['rate'], v['ceil'], v['prio']) for u, v in selected.items()] + [('99', .1, 10, 7)]
    for number, rate, ceil, prio in classes:
        rows += [f'class add dev eth0 parent 1:1 classid 1:{number} htb rate {rate}mbit ceil {ceil}mbit prio {prio} burst 32768 cburst 32768 quantum 1514',
                 f'qdisc add dev eth0 parent 1:{number} handle {number}: fq_codel limit 256 target 5ms interval 100ms noecn']
    if mode != 'shared':
        for user in spec['users']:
            number = '10' if user['name'] == 'ue1' else '20'
            for proto in [6, 17]:
                rows.append(f'filter add dev eth0 parent 1: protocol ip prio 10 u32 match ip protocol {proto} 0xff match ip sport {user["port"]} 0xffff flowid 1:{number}')
            rows.append(f'filter add dev eth0 parent 1: protocol ip prio 5 u32 match ip protocol 1 0xff match ip tos {user["pingTos"]} 0xfc flowid 1:{number}')
    return rows


def apply_mode(mode, folder):
    before = tc_state()
    roots = [q for q in before['qdisc'] if q.get('root')]
    require(len(roots) == 1 and ((roots[0]['kind'] == 'noqueue' and roots[0]['handle'] == '0:') or
            (roots[0]['kind'] == 'htb' and roots[0]['handle'] == '1:')), 'Foreign root qdisc; refusing overwrite')
    commands = tc_commands(mode)
    if roots[0]['kind'] == 'htb':
        commands.insert(0, 'qdisc del dev eth0 root handle 1:')
    m1.save(folder, 'tc-before.json', before)
    m1.save(folder, 'tc-batch.txt', '\n'.join(commands) + '\n')
    if commands:
        result = subprocess.run(['kubectl', '-n', NS, 'exec', '-i', 'deployment/' + NAME, '-c', 'queue', '--',
                                 'tc', '-batch', '-'], input='\n'.join(commands) + '\n', text=True,
                                 capture_output=True, timeout=30)
        m1.save(folder, 'tc-apply.txt', {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
        require(result.returncode == 0, 'TC batch failed; restore before retry')
    after = tc_state()
    m1.save(folder, 'tc-after.json', after)
    return after


def verify(folder):
    folder.mkdir()
    old, topology = a.verify_fixture(folder / 'a-fixture')
    state = snapshot()
    m1.assert_steady(state, state)
    a.same_original(old, state)
    require(len(state[NS]) == 1, 'Contention Pod count drift')
    pod, value = m1.one_pod(state, NS, NAME)
    require(value['node'] == 'lhm-virtual-machine' and len(value['containers']) == 3, 'Endpoint placement/container drift')
    image = m1.config()[1]['images']['backend']['image']
    require(all(c['imageID'].endswith(image.split('@')[1]) for c in value['containers']), 'Endpoint image drift')
    resources = []
    for desired in render()[1:]:
        actual = json.loads(m1.kube('-n', NS, 'get', desired['kind'], NAME, '-o', 'json').stdout)
        a.m3.require_subset(desired['spec'], actual['spec'])
        resources.append(actual)
    require(len(json.loads(m1.kube('-n', NS, 'get', 'pods', '-o', 'json').stdout)['items']) == 1, 'Extra test clients')
    actual_pod = json.loads(m1.kube('-n', NS, 'get', 'pod', pod, '-o', 'json').stdout)
    require(not actual_pod['spec'].get('hostNetwork', False) and not actual_pod['spec'].get('hostPID', False) and
            not any('hostPath' in v for v in actual_pod['spec']['volumes']), 'Host namespace/mount not allowed')
    cap = queue('cat', '/proc/self/status')
    require('CapEff:\t0000000000001000' in cap, 'Queue container must have only NET_ADMIN')
    switch = m1.kube('-n', 'free5gc', 'exec', 'deployment/free5gc-helm-free5gc-upf-upf', '--', 'cat', '/proc/gtp5g/qos').stdout
    require(switch.strip() == 'QoS Enable: 0', 'Native UPF policer must remain disabled')
    for user in config()['users']:
        live = topology['users'][user['name']]
        sessions = (folder / 'a-fixture' / (user['name'] + '-sessions.txt')).read_text()
        require(sessions.count('PDU Session') == 1 and 'ambr: up[1000Mb/s] down[1000Mb/s]' in sessions, 'Unexpected session/AMBR')
        require(m1.kube('-n', 'free5gc', 'exec', live['pod'], '--', 'pgrep', '-x', 'ping', required=False).returncode == 1,
                'An unrelated ping process is already active')
        path = m1.kube('-n', 'free5gc', 'exec', live['pod'], '--', 'ip', '-4', 'route', 'get', value['ip'], 'from', live['ip']).stdout
        require('dev uesimtun0' in path, 'RTT probe would bypass tunnel')
        m1.save(folder, user['name'] + '-rtt-route.txt', path)
    endpoint = json.loads(m1.kube('-n', NS, 'get', 'endpoints', NAME, '-o', 'json').stdout)
    require([x['ip'] for s in endpoint['subsets'] for x in s['addresses']] == [value['ip']], 'Wrong shared endpoint')
    topology['contention'] = {'pod': pod, **value, 'resources': resources, 'endpoint': endpoint}
    m1.save(folder, 'capabilities.txt', cap)
    m1.save(folder, 'qos-switch.txt', switch)
    m1.save(folder, 'topology.json', topology)
    m1.save(folder, 'pods.json', state)
    m1.save(folder, 'tc.json', tc_state())
    return state, topology


def deploy(out):
    before, _ = a.verify_fixture(out / 'before')
    require(not m1.kube('get', 'namespace', NS, '--ignore-not-found', '-o', 'name').stdout.strip(),
            'Contention namespace exists; inspect instead of redeploy')
    services = json.loads(m1.kube('get', 'services', '-A', '-o', 'json').stdout)['items']
    ports = {u['nodePort'] for u in config()['users']}
    require(not ports.intersection(p.get('nodePort') for s in services for p in s['spec'].get('ports', [])), 'NodePort collision')
    m1.save(out, 'apply.txt', m1.kube('apply', '-f', out / 'manifest.yaml').stdout)
    m1.save(out, 'rollout.txt', m1.kube('-n', NS, 'rollout', 'status', 'deployment/' + NAME, '--timeout=90s', timeout=100).stdout)
    state, _ = verify(out / 'after')
    a.same_original(before, state)
    require(all(q['kind'] == 'noqueue' for q in tc_state()['qdisc']), 'Unexpected initial shaping')


def ping_args(user, topology, count):
    live = topology['users'][user['name']]
    return ['kubectl', '-n', 'free5gc', 'exec', live['pod'], '--', 'ping', '-4', '-n', '-D',
            '-Q', str(user['pingTos']), '-I', 'uesimtun0', '-i', '.2', '-s', '56', '-c', str(count),
            '-w', '33', '-W', '2', topology['contention']['ip']]


def mapping_check(out, topology):
    folder = out / 'probe-mapping'
    folder.mkdir()
    apply_mode('equal', folder)
    for user in config()['users']:
        before = tc_state()
        args = ping_args(user, topology, 5)
        result = m1.run(args, timeout=15)
        m1.save(folder, user['name'] + '-command.json', args)
        m1.save(folder, user['name'] + '-ping.txt', result.stdout)
        after = tc_state()
        m1.save(folder, user['name'] + '-before.json', before)
        m1.save(folder, user['name'] + '-after.json', after)
        require('0% packet loss' in result.stdout and '100% packet loss' not in result.stdout, 'Class probe ping failed')
        def counts(state):
            return {c['handle']: c.get('packets', 0) for c in state['class']}
        b, e = counts(before), counts(after)
        own, other = ('1:10', '1:20') if user['name'] == 'ue1' else ('1:20', '1:10')
        require(e[own] - b[own] >= 5 and e[other] == b[other], 'RTT probe not mapped to its user class')


def run_group(out, label, mode, number, state, topology):
    folder = out / label
    folder.mkdir()
    apply_mode(mode, folder)
    m1.assert_steady(state, snapshot())
    upf, _ = m1.one_pod(state, 'free5gc', 'free5gc-helm-free5gc-upf-upf')
    initial = m1.upf_observation(folder, 'before', upf)
    m1.save(folder, 'qos-before.txt', native_switch())
    jobs, samples = [], []
    result = {'outcome': 'failed', 'mode': mode, 'round': number}
    started = m1.now()
    try:
        for user in config()['users']:
            sub = folder / user['name']
            sub.mkdir()
            live = topology['users'][user['name']]
            args = ['kubectl', '-n', 'free5gc', 'exec', live['pod'], '--', 'iperf3', '-c', topology['target'],
                    '-B', live['ip'], '-p', str(user['nodePort']), '-u', '-R', '-P', '1', '-b', '8M',
                    '-l', '1200', '-t', '30', '--tos', str(user['pingTos']), '-J', '--get-server-output', '--connect-timeout', '5000']
            for kind, command in [('iperf', args), ('ping', ping_args(user, topology, 150))]:
                m1.save(sub, kind + '-command.json', command)
                stdout, stderr = (sub / (kind + ('.json' if kind == 'iperf' else '.txt'))).open('w'), (sub / (kind + '-stderr.txt')).open('w')
                job = subprocess.Popen(command, stdout=stdout, stderr=stderr, text=True)
                jobs.append((user, kind, job, stdout, stderr))
        deadline = time.monotonic() + 60
        while any(j.poll() is None for _, _, j, _, _ in jobs):
            current = snapshot()
            m1.save(folder, 'guard-pods.json', current)
            m1.assert_steady(state, current)
            memory = int(m1.kube('-n', 'free5gc', 'exec', upf, '--', 'cat', '/sys/fs/cgroup/memory.current', timeout=5).stdout)
            samples.append({'time': m1.now(), 'upfMemoryBytes': memory})
            m1.save(folder, 'resource-samples.json', samples)
            require(memory < 536870912 and time.monotonic() < deadline, 'Memory/deadline guard')
            time.sleep(.5)
        for _, _, job, stdout, stderr in jobs:
            stdout.close()
            stderr.close()
            require(job.returncode == 0, 'Receiver or RTT process failed')
        result['startedAt'], result['endedAt'] = started, m1.now()
        m1.save(folder, 'tc-end.json', tc_state())
        m1.save(folder, 'qos-after.txt', native_switch())
        final = m1.upf_observation(folder, 'after', upf)
        require(all(y > x for x, y in zip(m1.path_counters(initial['interfaces']), m1.path_counters(final['interfaces']))),
                'Missing UPF bidirectional counters')
        for user in config()['users']:
            m1.save(folder, user['name'] + '-server.txt', m1.kube('-n', NS, 'logs', topology['contention']['pod'],
                    '-c', user['name'], '--since-time=' + started).stdout)
        m1.assert_steady(state, snapshot())
        result['outcome'] = 'captured'
        m1.save(folder, 'summary.json', result)
        audit_spec = importlib.util.spec_from_file_location('audit_contention', ROOT / 'scripts/audit-cnf-contention.py')
        auditor = importlib.util.module_from_spec(audit_spec)
        audit_spec.loader.exec_module(auditor)
        observation = auditor.group(folder, topology, config())
        m1.save(folder, 'observation.json', observation)
        print(label + ': ' + json.dumps({u: observation['users'][u]['steadyMbps'] for u in ['ue1', 'ue2']}), flush=True)
        return {'folder': label, 'mode': mode, 'round': number, **observation}
    except Exception as exc:
        result['outcome'], result['error'] = 'failed', str(exc)
        raise
    finally:
        for user, kind, job, stdout, stderr in jobs:
            if job.poll() is None:
                job.terminate()
                m1.kube('-n', 'free5gc', 'exec', topology['users'][user['name']]['pod'], '--',
                        'pkill', '-TERM', '-x', 'iperf3' if kind == 'iperf' else 'ping', required=False)
                job.wait(timeout=10)
            stdout.close()
            stderr.close()
        m1.save(folder, 'summary.json', result)


def measure(out, formal):
    state, topology = verify(out / 'initial')
    require(all(q['kind'] == 'noqueue' for q in tc_state()['qdisc']), 'Start from unshaped endpoint')
    rows = []
    try:
        mapping_check(out, topology)
        rows.append(run_group(out, 'unshaped-before', 'none', 0, state, topology))
        for n in range(1, 4 if formal else 2):
            for mode in config()['modes']:
                rows.append(run_group(out, f'{n:02d}-{mode}', mode, n, state, topology))
                m1.save(out, 'groups.json', rows)
        # An impossible request must be rejected without touching active TC.
        before = tc_state()
        try:
            admit({'ue1': {'rate': 6, 'ceil': 8, 'prio': 0}, 'ue2': {'rate': 5, 'ceil': 8, 'prio': 1}})
        except RuntimeError as exc:
            m1.save(out, 'admission-rejection.json', {'outcome': 'rejected', 'reason': str(exc), 'before': before, 'after': tc_state()})
        else:
            raise RuntimeError('Impossible request was admitted')
    finally:
        restore = out / 'restore'
        restore.mkdir()
        apply_mode('none', restore)
    rows.append(run_group(out, 'unshaped-after', 'none', 0, state, topology))
    m1.save(out, 'groups.json', rows)
    final, _ = verify(out / 'final')
    m1.assert_steady(state, final)
    return {'groups': len(rows), 'sessions': len(rows) * 2, 'mode': 'formal' if formal else 'pilot',
            'original24PodIdentitiesUnchanged': True, 'newEndpointPods': 1, 'restoredNoqueue': True,
            'acceptance': 'independent audit required; RTT/loss observations do not imply an application SLA'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['render', 'deploy', 'check', 'pilot', 'measure', 'restore'])
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    inputs = [SPEC, Path(__file__), ROOT / 'scripts/audit-cnf-contention.py', ROOT / 'tests/test_cnf_contention.py',
              a.SPEC, ROOT / 'scripts/cnf-multi-ue.py', ROOT / 'scripts/create-test-subscriber.sh',
              ROOT / 'infra/free5gc-test-subscriber-ue2.json', a.base.PROFILE, ROOT / 'scripts/cnf-profile.py',
              *[ROOT / p for p in a.base.profile()['inputSha256LF']]]
    m1.save(args.out, 'metadata.json', {'route': 'R2', 'stage': 'R2.S4', 'task': 'C1', 'configVersion': VERSION,
        'command': args.command, 'startedAt': m1.now(), 'sha256LF': {str(p.relative_to(ROOT)): a.m2.sha(p) for p in inputs}})
    for path in inputs:
        target = args.out / 'inputs' / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    m1.save(args.out, 'experiment.json', config())
    m1.save(args.out, 'manifest.yaml', yaml.safe_dump_all(render(), sort_keys=False))
    result = {'outcome': 'failed', 'command': args.command}
    try:
        if args.command == 'deploy':
            deploy(args.out)
        elif args.command == 'check':
            verify(args.out / 'live')
        elif args.command == 'restore':
            apply_mode('none', args.out)
            verify(args.out / 'live')
        elif args.command in ['pilot', 'measure']:
            result['evidence'] = measure(args.out, args.command == 'measure')
        result['outcome'] = 'prepared' if args.command == 'render' else 'captured' if args.command in ['pilot', 'measure'] else 'passed'
    except Exception as exc:
        result['error'] = str(exc)
        raise
    finally:
        result['endedAt'] = m1.now()
        m1.save(args.out, 'summary.json', result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Contention ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(1)
