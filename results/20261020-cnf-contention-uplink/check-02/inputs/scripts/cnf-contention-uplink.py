#!/usr/bin/env python3
"""R2.S4/C2: two UE senders, one container-local ingress IFB bottleneck."""
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
SPEC = ROOT / 'infra/cnfs/contention-uplink-experiment.json'
NS, NAME, VERSION, DEVICE = 'cnf-contention-uplink', 'cnf-c2-endpoint', 'cnf-contention-uplink-v1', 'cnfc2ifb0'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


d = load('c1', ROOT / 'scripts/cnf-contention.py')
audit = load('audit_c2', ROOT / 'scripts/audit-cnf-contention-uplink.py')
a, m1, require = d.a, d.m1, d.require


def config():
    value = json.loads(SPEC.read_text(encoding='utf-8'))
    expected = copy.deepcopy(d.config())
    expected.update(configVersion=VERSION, task='C2', namespace=NS, deployment=NAME,
                    direction='uplink', queueDevice=DEVICE, scope=value['scope'])
    for i, user in enumerate(expected['users']):
        user['nodePort'] = 31403 + i
    require(value == expected, 'Unknown uplink experiment; frozen C1 thresholds must remain intact')
    return value


def render():
    docs = copy.deepcopy(d.render())
    for item in docs:
        item['metadata']['name'] = NS if item['kind'] == 'Namespace' else NAME
        if item['kind'] != 'Namespace':
            item['metadata']['namespace'] = NS
        labels = item['metadata'].get('labels', {})
        labels.update({'app.kubernetes.io/part-of': VERSION})
        if 'app' in labels:
            labels['app'] = NAME
        if item['kind'] == 'Deployment':
            item['spec']['selector']['matchLabels']['app'] = NAME
            item['spec']['template']['metadata']['labels'] = copy.deepcopy(labels)
            for container in item['spec']['template']['spec']['containers'][:2]:
                container['args'] = ['-s', '-p', container['args'][2], '-J', '--forceflush']
        elif item['kind'] == 'Service':
            item['spec']['selector']['app'] = NAME
            for port in item['spec']['ports']:
                port['nodePort'] += 2
        elif item['kind'] == 'NetworkPolicy':
            item['spec']['podSelector']['matchLabels']['app'] = NAME
    return docs


def queue(*args, **kwargs):
    return m1.kube('-n', NS, 'exec', 'deployment/' + NAME, '-c', 'queue', '--', *args, **kwargs)


def snapshot():
    state = d.snapshot()
    items = json.loads(m1.kube('-n', NS, 'get', 'pods', '-o', 'json').stdout)['items']
    state[NS] = {}
    for p in items:
        require(not p['metadata'].get('deletionTimestamp'), 'Uplink Pod terminating')
        state[NS][p['metadata']['name']] = {'uid': p['metadata']['uid'], 'node': p['spec']['nodeName'],
            'ip': p['status'].get('podIP'), 'phase': p['status']['phase'], 'containers': [
                {k: c.get(k) for k in ['name', 'image', 'imageID', 'containerID', 'ready', 'restartCount', 'state']}
                for c in p['status'].get('containerStatuses', [])]}
    return state


def network():
    link = queue('ip', '-j', '-d', 'link', 'show', 'dev', DEVICE, required=False)
    require(link.returncode == 0 or 'does not exist' in link.stderr, 'Cannot determine IFB existence')
    value = {'eth0Qdisc': json.loads(queue('tc', '-j', '-s', 'qdisc', 'show', 'dev', 'eth0').stdout),
             'ingressFilters': json.loads(queue('tc', '-j', '-s', 'filter', 'show', 'dev', 'eth0', 'parent', 'ffff:').stdout or '[]'),
             'link': json.loads(link.stdout) if link.returncode == 0 else []}
    if value['link']:
        raw = {'qdisc': queue('tc', '-j', '-s', 'qdisc', 'show', 'dev', DEVICE).stdout,
               'class': queue('tc', '-s', '-d', 'class', 'show', 'dev', DEVICE).stdout,
               'filter': queue('tc', '-s', 'filter', 'show', 'dev', DEVICE).stdout}
        value['tc'] = audit.base.parse_tc_native(json.loads(raw['qdisc']), raw['class'], raw['filter']) | {'raw': raw}
    return value


def verify(folder):
    folder.mkdir()
    old, topology = d.verify(folder / 'c1')
    audit.base.check_tc(json.loads((folder / 'c1/tc.json').read_text()), 'none')
    state = snapshot()
    a.same_original(old, state)
    audit.identities(state)
    pod, value = m1.one_pod(state, NS, NAME)
    require(value['node'] == 'lhm-virtual-machine' and len(value['containers']) == 3, 'Wrong uplink endpoint')
    resources = []
    for desired in render()[1:]:
        actual = json.loads(m1.kube('-n', NS, 'get', desired['kind'], NAME, '-o', 'json').stdout)
        a.m3.require_subset(desired['spec'], actual['spec'])
        resources.append(actual)
    live = json.loads(m1.kube('-n', NS, 'get', 'pod', pod, '-o', 'json').stdout)
    require(not live['spec'].get('hostNetwork', False) and not live['spec'].get('hostPID', False) and
            not any('hostPath' in v for v in live['spec']['volumes']), 'Host access disallowed')
    image = m1.config()[1]['images']['backend']['image']
    require(all(c['imageID'].endswith(image.split('@')[1]) for c in value['containers']), 'Image drift')
    cap = queue('cat', '/proc/self/status').stdout
    require('CapEff:\t0000000000001000' in cap, 'Only NET_ADMIN is permitted')
    endpoint = json.loads(m1.kube('-n', NS, 'get', 'endpoints', NAME, '-o', 'json').stdout)
    require([x['ip'] for s in endpoint['subsets'] for x in s['addresses']] == [value['ip']], 'Wrong service endpoint')
    for user in config()['users']:
        u = topology['users'][user['name']]
        route = m1.kube('-n', 'free5gc', 'exec', u['pod'], '--', 'ip', '-4', 'route', 'get', value['ip'], 'from', u['ip']).stdout
        require('dev uesimtun0' in route, 'Probe bypasses UE tunnel')
        m1.save(folder, user['name'] + '-rtt-route.txt', route)
    topology['uplink'] = {'pod': pod, **value, 'resources': resources, 'endpoint': endpoint}
    m1.save(folder, 'topology.json', topology)
    m1.save(folder, 'pods.json', state)
    m1.save(folder, 'network.json', network())
    m1.save(folder, 'capabilities.txt', cap)
    return state, topology


def deploy(out):
    before, _ = d.verify(out / 'before')
    require(not m1.kube('get', 'namespace', NS, '--ignore-not-found', '-o', 'name').stdout.strip(), 'Namespace exists; inspect')
    ports = {u['nodePort'] for u in config()['users']}
    services = json.loads(m1.kube('get', 'services', '-A', '-o', 'json').stdout)['items']
    require(not ports.intersection(p.get('nodePort') for s in services for p in s['spec'].get('ports', [])), 'NodePort collision')
    m1.save(out, 'apply.txt', m1.kube('apply', '-f', out / 'manifest.yaml').stdout)
    m1.save(out, 'rollout.txt', m1.kube('-n', NS, 'rollout', 'status', 'deployment/' + NAME, '--timeout=90s', timeout=100).stdout)
    after, _ = verify(out / 'after')
    a.same_original(before, after)
    audit.check_network(network(), clean=True)


def prepare(folder):
    folder.mkdir()
    before = network()
    audit.check_network(before, clean=True)
    m1.save(folder, 'before.json', before)
    commands = [
        ['ip', 'link', 'add', 'name', DEVICE, 'alias', VERSION, 'mtu', '1450', 'type', 'ifb'],
        ['ip', 'link', 'set', 'dev', DEVICE, 'alias', VERSION, 'txqueuelen', '0', 'up'],
        ['tc', 'qdisc', 'add', 'dev', 'eth0', 'handle', 'ffff:', 'ingress'],
        ['tc', 'filter', 'add', 'dev', 'eth0', 'parent', 'ffff:', 'protocol', 'ip', 'pref', '45000', 'handle', '1',
         'matchall', 'action', 'mirred', 'egress', 'redirect', 'dev', DEVICE]]
    m1.save(folder, 'commands.json', commands)
    for index, args in enumerate(commands):
        queue(*args)
        if index == 1:
            links = json.loads(queue('ip', '-j', '-d', 'link', 'show', 'dev', DEVICE).stdout)
            m1.save(folder, 'created-link.json', links)
            audit.check_link(links)
    after = network()
    audit.check_network(after)
    audit.check_tc(after['tc'], 'none')
    m1.save(folder, 'after.json', after)


def tc_commands(mode):
    # Same HTB contract as C1, but server destination ports on ingress IFB.
    return [row.replace('dev eth0', 'dev ' + DEVICE).replace('match ip sport', 'match ip dport')
            for row in d.tc_commands(mode)]


def apply_mode(mode, folder):
    before = network()
    audit.check_network(before)
    audit.known_queue(before['tc'])
    commands = tc_commands(mode)
    root = next(q for q in before['tc']['qdisc'] if q.get('root'))
    if root['kind'] == 'htb':
        commands.insert(0, 'qdisc del dev ' + DEVICE + ' root handle 1:')
    m1.save(folder, 'network-before.json', before)
    m1.save(folder, 'tc-batch.txt', '\n'.join(commands) + '\n')
    if commands:
        result = subprocess.run(['kubectl', '-n', NS, 'exec', '-i', 'deployment/' + NAME, '-c', 'queue', '--', 'tc', '-batch', '-'],
                                input='\n'.join(commands) + '\n', text=True, capture_output=True, timeout=30)
        m1.save(folder, 'tc-apply.json', {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
        require(result.returncode == 0, 'TC configuration failed')
    after = network()
    audit.check_network(after)
    audit.check_tc(after['tc'], mode)
    m1.save(folder, 'network-start.json', after)


def restore(folder):
    folder.mkdir()
    value = network()
    m1.save(folder, 'before.json', value)
    if value['link']:
        audit.check_link(value['link'])
        audit.known_queue(value['tc'])
        ingress = [q for q in value['eth0Qdisc'] if not q.get('root')]
        if ingress:
            audit.check_ingress(value, allow_empty=True)
            queue('tc', 'qdisc', 'del', 'dev', 'eth0', 'handle', 'ffff:', 'ingress')
        else:
            require(not value['ingressFilters'], 'Foreign ingress filters')
        queue('ip', 'link', 'del', 'dev', DEVICE)
    after = network()
    audit.check_network(after, clean=True)
    m1.save(folder, 'after.json', after)


def ping_args(user, topology, count):
    mapped = copy.deepcopy(topology)
    mapped['contention'] = topology['uplink']
    return d.ping_args(user, mapped, count)


def mapping_check(out, topology):
    folder = out / 'probe-mapping'
    folder.mkdir()
    apply_mode('equal', folder)
    for user in config()['users']:
        before = network()
        command = ping_args(user, topology, 5)
        m1.save(folder, user['name'] + '-command.json', command)
        m1.save(folder, user['name'] + '-ping.txt', m1.run(command, timeout=15).stdout)
        after = network()
        m1.save(folder, user['name'] + '-before.json', before)
        m1.save(folder, user['name'] + '-after.json', after)
        audit.check_probe_mapping(before, after, user['name'])


def run_group(out, label, mode, number, state, topology):
    folder = out / label
    folder.mkdir()
    apply_mode(mode, folder)
    m1.assert_steady(state, snapshot())
    upf, _ = m1.one_pod(state, 'free5gc', 'free5gc-helm-free5gc-upf-upf')
    initial = m1.upf_observation(folder, 'before', upf)
    m1.save(folder, 'qos-before.txt', d.native_switch())
    jobs, samples = [], []
    result = {'outcome': 'failed', 'mode': mode, 'round': number, 'startedAt': m1.now()}
    try:
        for user in config()['users']:
            sub = folder / user['name']
            sub.mkdir()
            u = topology['users'][user['name']]
            command = ['kubectl', '-n', 'free5gc', 'exec', u['pod'], '--', 'iperf3', '-c', topology['target'],
                       '-B', u['ip'], '-p', str(user['nodePort']), '-u', '-P', '1', '-b', '8M', '-l', '1200',
                       '-t', '30', '--tos', str(user['pingTos']), '-J', '--get-server-output', '--connect-timeout', '5000']
            for kind, args in [('iperf', command), ('ping', ping_args(user, topology, 150))]:
                m1.save(sub, kind + '-command.json', args)
                stdout = (sub / (kind + ('.json' if kind == 'iperf' else '.txt'))).open('w')
                stderr = (sub / (kind + '-stderr.txt')).open('w')
                jobs.append((u, kind, subprocess.Popen(args, stdout=stdout, stderr=stderr, text=True), stdout, stderr))
        deadline = time.monotonic() + 65
        while any(job.poll() is None for _, _, job, _, _ in jobs):
            current = snapshot()
            m1.save(folder, 'guard-pods.json', current)
            m1.assert_steady(state, current)
            memory = int(m1.kube('-n', 'free5gc', 'exec', upf, '--', 'cat', '/sys/fs/cgroup/memory.current', timeout=5).stdout)
            samples.append({'time': m1.now(), 'upfMemoryBytes': memory})
            m1.save(folder, 'resource-samples.json', samples)
            require(memory < config()['maximumUpfMemoryBytes'] and time.monotonic() < deadline, 'Memory/deadline guard')
            time.sleep(.5)
        for _, _, job, stdout, stderr in jobs:
            stdout.close()
            stderr.close()
            require(job.returncode == 0, 'Sender/probe process failed')
        m1.save(folder, 'network-end.json', network())
        m1.save(folder, 'qos-after.txt', d.native_switch())
        final = m1.upf_observation(folder, 'after', upf)
        require(all(y > x for x, y in zip(m1.path_counters(initial['interfaces']), m1.path_counters(final['interfaces']))), 'UPF path counters')
        for user in config()['users']:
            m1.save(folder, user['name'] + '-server.txt', m1.kube('-n', NS, 'logs', topology['uplink']['pod'],
                    '-c', user['name'], '--since-time=' + result['startedAt']).stdout)
        m1.assert_steady(state, snapshot())
        result.update(outcome='captured', endedAt=m1.now())
        m1.save(folder, 'summary.json', result)
        observation = audit.group(folder, topology, config())
        m1.save(folder, 'observation.json', observation)
        print(label + ': ' + json.dumps({u: observation['users'][u]['steadyMbps'] for u in ['ue1', 'ue2']}), flush=True)
        return {'folder': label, 'mode': mode, 'round': number, **observation}
    except Exception as exc:
        result.update(outcome='failed', error=str(exc))
        raise
    finally:
        for u, kind, job, stdout, stderr in jobs:
            if job.poll() is None:
                job.terminate()
                m1.kube('-n', 'free5gc', 'exec', u['pod'], '--', 'pkill', '-TERM', '-x', 'iperf3' if kind == 'iperf' else 'ping', required=False)
                job.wait(timeout=10)
            stdout.close()
            stderr.close()
        m1.save(folder, 'summary.json', result)


def measure(out, formal):
    state, topology = verify(out / 'initial')
    audit.check_network(network(), clean=True)
    rows = []
    try:
        prepare(out / 'prepare')
        mapping_check(out, topology)
        rows.append(run_group(out, 'unshaped-before', 'none', 0, state, topology))
        for number in range(1, 4 if formal else 2):
            for mode in config()['modes']:
                rows.append(run_group(out, f'{number:02d}-{mode}', mode, number, state, topology))
                m1.save(out, 'groups.json', rows)
        before = network()
        try:
            d.admit({'ue1': {'rate': 6, 'ceil': 8, 'prio': 0}, 'ue2': {'rate': 5, 'ceil': 8, 'prio': 1}})
        except RuntimeError as exc:
            m1.save(out, 'admission-rejection.json', {'outcome': 'rejected', 'reason': str(exc), 'before': before, 'after': network()})
        else:
            raise RuntimeError('Overcapacity request accepted')
        # Also repeat capacity with ingress plumbing present but no HTB limit.
        rows.append(run_group(out, 'unshaped-after', 'none', 0, state, topology))
        m1.save(out, 'groups.json', rows)
    finally:
        restore(out / 'restore')
    final, _ = verify(out / 'final')
    m1.assert_steady(state, final)
    return {'groups': len(rows), 'sessions': len(rows) * 2, 'original25PodIdentitiesUnchanged': True,
            'all26PodIdentitiesUnchanged': True, 'restoredNoIngressOrIfb': True,
            'acceptance': 'independent audit required; no business RTT/loss SLA'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['render', 'deploy', 'check', 'pilot', 'measure', 'restore'])
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    config()
    args.out.mkdir(parents=True, exist_ok=False)
    inputs = [SPEC, Path(__file__), ROOT / 'scripts/audit-cnf-contention-uplink.py', ROOT / 'tests/test_cnf_contention_uplink.py',
              d.SPEC, ROOT / 'scripts/cnf-contention.py', ROOT / 'scripts/audit-cnf-contention.py',
              a.SPEC, ROOT / 'scripts/cnf-multi-ue.py', ROOT / 'scripts/create-test-subscriber.sh',
              ROOT / 'infra/free5gc-test-subscriber-ue2.json', a.base.PROFILE, ROOT / 'scripts/cnf-profile.py',
              *[ROOT / p for p in a.base.profile()['inputSha256LF']]]
    m1.save(args.out, 'metadata.json', {'route': 'R2', 'stage': 'R2.S4', 'task': 'C2', 'configVersion': VERSION,
        'command': args.command, 'startedAt': m1.now(), 'sha256LF': {p.relative_to(ROOT).as_posix(): a.m2.sha(p) for p in inputs}})
    for p in inputs:
        target = args.out / 'inputs' / p.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(p.read_bytes())
    m1.save(args.out, 'experiment.json', config())
    m1.save(args.out, 'manifest.yaml', yaml.safe_dump_all(render(), sort_keys=False))
    result = {'outcome': 'failed', 'command': args.command}
    try:
        if args.command == 'deploy':
            deploy(args.out)
        elif args.command == 'check':
            verify(args.out / 'live')
            audit.check_network(network(), clean=True)
        elif args.command == 'restore':
            restore(args.out / 'restore')
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
        print('Uplink contention ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(1)
