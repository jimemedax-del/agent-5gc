#!/usr/bin/env python3
"""Fixed R2.S3 CNF states, controlled deployment/recovery and immutable measurements."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / 'infra/cnfs/service-chain-profile.json'
module = importlib.util.spec_from_file_location('netpol', ROOT / 'scripts/cnf-networkpolicy.py')
np = importlib.util.module_from_spec(module)
module.loader.exec_module(np)
m4, m3, m2, m1 = np.m4, np.m3, np.m2, np.m1


class RecoveryExercise(RuntimeError):
    """Intentional failure after a real state change, used only by recovery-check."""


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def profile():
    spec = json.loads(PROFILE.read_text(encoding='utf-8'))
    for relative, expected in spec['inputSha256LF'].items():
        if m2.sha(ROOT / relative) != expected:
            raise ValueError('Pinned input changed: ' + relative)
    return spec


def render(target):
    spec = profile()
    if target not in spec['states']:
        raise ValueError('Unknown fixed state')
    original = m1.config()[0]
    inherited = m2.render()[2]
    backends = m3.render()[3] if target == 'qos' else m4.render(target in ['placed', 'protected'])[3]
    # Fixed CNF resources only; no Namespace/5GC mutations or whole-namespace deletion.
    docs = copy.deepcopy([d for d in original if d['kind'] == 'ConfigMap' or
                          d['kind'] == 'Service' and d['metadata']['name'] == 'cnf-m1-backend'])
    docs += copy.deepcopy([d for d in inherited if d['kind'] in ['ConfigMap', 'Service'] or
                           d['kind'] == 'Deployment' and d['metadata']['name'] == 'cnf-m1-envoy'])
    docs += copy.deepcopy(backends)
    if target == 'protected':
        docs += np.render()[5]
    if any(d['metadata']['namespace'] != m1.NS or d['kind'] not in
           ['ConfigMap', 'Service', 'Deployment', 'NetworkPolicy'] for d in docs):
        raise ValueError('Resource outside the fixed CNF boundary')
    return docs


def inventory():
    objects = json.loads(m1.kube('-n', m1.NS, 'get',
        'deployments,services,configmaps,networkpolicies,statefulsets,daemonsets,jobs,cronjobs', '-o', 'json').stdout)['items']
    return {(o['kind'], o['metadata']['name']): o for o in objects
            if (o['kind'], o['metadata']['name']) != ('ConfigMap', 'kube-root-ca.crt')}


def match(target, objects):
    expected = {(o['kind'], o['metadata']['name']): o for o in render(target)}
    if set(objects) != set(expected):
        raise RuntimeError('Resource set differs from fixed state ' + target)
    for key, wanted in expected.items():
        actual = objects[key]
        field = 'data' if key[0] == 'ConfigMap' else 'spec'
        m3.require_subset(wanted[field], actual[field])
        if key[0] == 'ConfigMap' and actual[field] != wanted[field]:
            raise RuntimeError('Unexpected ConfigMap data')
        if key[0] == 'NetworkPolicy' and actual[field] != wanted[field]:
            raise RuntimeError('Unexpected additive policy rule')
        if key[0] == 'Deployment':
            live = actual['spec']['template']['spec']
            if live.get('hostNetwork') or live.get('initContainers') or live.get('hostPID') or live.get('hostIPC'):
                raise RuntimeError('Unexpected host namespace or init containers')
            for container, required in zip(live['containers'], wanted['spec']['template']['spec']['containers']):
                if container.get('env') or container.get('envFrom') or container['securityContext'] != required['securityContext']:
                    raise RuntimeError('Unexpected environment or privileges')


def detect(objects):
    matches = []
    for target in profile()['states']:
        try:
            match(target, objects)
            matches.append(target)
        except RuntimeError:
            pass
    if len(matches) != 1:
        raise RuntimeError('Live configuration is not one unique fixed state; refuse to overwrite drift')
    return matches[0]


def verify(out, target=None):
    out.mkdir()
    objects = inventory()
    current = detect(objects)
    if target and current != target:
        raise RuntimeError('Live state differs from requested target')
    state = m1.snapshot()
    m1.assert_steady(state, state)
    placement, routing, images, _ = m4.render(True)
    control, node_ip = m1.target_node()
    edge = m4.node_check(out, placement, images['images']['backend']['image'])
    expected_nodes = {'core': control['metadata']['name'], 'edge': edge['metadata']['name']
                      if current in ['placed', 'protected'] else control['metadata']['name']}
    envoy_name, envoy = m1.one_pod(state, m1.NS, routing['gatewayDeployment'])
    m1.verify_image_lock(envoy, images['images']['envoy']['image'])
    if envoy['node'] != control['metadata']['name']:
        raise RuntimeError('Envoy must stay on the control node')
    backends = {}
    for route in routing['classes']:
        name, pod = m1.one_pod(state, m1.NS, route['backendDeployment'])
        if pod['node'] != expected_nodes[route['name']]:
            raise RuntimeError('Backend scheduled on wrong node')
        for container in pod['containers']:
            m1.verify_image_lock({'containers': [container]}, images['images']['backend']['image'])
        if len(pod['containers']) != (2 if current == 'qos' else 1):
            raise RuntimeError('Wrong backend container count')
        live_pod = json.loads(m1.kube('-n', m1.NS, 'get', 'pod', name, '-o', 'json').stdout)['spec']
        desired = next(d['spec']['template']['spec'] for d in render(current)
                       if d['kind'] == 'Deployment' and d['metadata']['name'] == route['backendDeployment'])
        m3.require_subset(desired, live_pod)
        endpoint = json.loads(m1.kube('-n', m1.NS, 'get', 'endpoints', route['backendService'], '-o', 'json').stdout)
        slices = json.loads(m1.kube('-n', m1.NS, 'get', 'endpointslices', '-l',
            'kubernetes.io/service-name=' + route['backendService'], '-o', 'json').stdout)['items']
        m4.validate_endpoint(endpoint, slices, name, pod, pod['node'])
        m1.save(out, route['name'] + '-endpoints.json', endpoint)
        m1.save(out, route['name'] + '-endpointslices.json', slices)
        if current == 'qos':
            m3.tbf(m3.tc_read(out, route['name'] + '-qdisc', name), m3.render()[0]['ratesMbps'][route['name']])
        else:
            m4.qdisc(out, route['name'] + '-qdisc.json', name)
        backends[route['name']] = (name, pod)
    if len(state[m1.NS]) != 3:
        raise RuntimeError('Unexpected CNF Pod; finish/inspect other experiments before proceeding')
    # Persist only validated specs/data and fixed labels, not arbitrary annotations or environments.
    safe = [{'kind': k[0], 'name': k[1], 'uid': v['metadata']['uid'],
             'body': v.get('data') if k[0] == 'ConfigMap' else v['spec']}
            for k, v in sorted(objects.items())]
    m1.save(out, 'resources.json', safe)
    m1.save(out, 'pods.json', state)
    m1.save(out, 'state.json', {'state': current, 'manifestSha256': digest(render(current))})
    services = {name: o for (kind, name), o in objects.items() if kind == 'Service'}
    return current, state, (node_ip, envoy_name, envoy, backends, services)


def snapshot(target, state):
    return {'profile': profile()['configVersion'], 'state': target, 'createdAt': m1.now(),
            'manifestSha256': digest(render(target)), 'pods': state,
            'scope': 'Only the fixed cnf-lab resource set; restore renders a known state, never arbitrary saved YAML'}


def validate_snapshot(value):
    if (value.get('profile') != profile()['configVersion'] or value.get('state') not in profile()['states'] or
            value.get('manifestSha256') != digest(render(value['state']))):
        raise ValueError('Snapshot is not a matching fixed profile/state; refuse arbitrary restoration')
    return value['state']


def apply_fixed(out, target):
    out.mkdir()
    docs = render(target)
    manifest = out / 'manifest.yaml'
    m1.save(out, manifest.name, yaml.safe_dump_all(docs, sort_keys=False))
    m1.save(out, 'server-dry-run.txt', m1.kube('apply', '--dry-run=server', '-f', manifest).stdout)
    if target != 'protected':
        m1.save(out, 'policies-remove.txt', m1.kube('delete', '-f', np.POLICY, '--ignore-not-found=true').stdout)
    else:
        m1.save(out, 'policies-first.txt', m1.kube('apply', '-f', np.POLICY).stdout)
    m1.save(out, 'apply.txt', m1.kube('apply', '-f', manifest, timeout=60).stdout)
    for name in ['cnf-m1-backend', 'cnf-m2-edge', 'cnf-m1-envoy']:
        m1.save(out, name + '-rollout.txt', m1.kube('-n', m1.NS, 'rollout', 'status', 'deployment/' + name,
            '--timeout=120s', timeout=130).stdout)
    time.sleep(5)  # Bounded policy/EndpointSlice propagation; actual verification follows.


def transition(out, target, command):
    initial = m1.baseline(out, 'before')
    previous, state, _ = verify(out / 'live-before')
    m1.assert_steady(initial, state)
    m1.save(out, 'snapshot.json', snapshot(previous, state))
    if command == 'recovery-check' and target == previous:
        raise ValueError('Recovery check must change state before exercising rollback')
    changed = False
    try:
        # Validate every target before any mutation, including rebuild deletion.
        m1.save(out, 'preflight-manifest.yaml', yaml.safe_dump_all(render(target), sort_keys=False))
        m1.save(out, 'preflight-dry-run.txt', m1.kube('apply', '--dry-run=server', '-f', out / 'preflight-manifest.yaml').stdout)
        changed = True
        if command == 'rebuild':
            m1.save(out, 'delete-deployments.txt', m1.kube('-n', m1.NS, 'delete', 'deployment',
                'cnf-m1-backend', 'cnf-m2-edge', 'cnf-m1-envoy', '--wait=true', timeout=90).stdout)
        apply_fixed(out / 'forward', target)
        _, after, _ = verify(out / 'live-applied', target)
        m1.assert_steady({m1.CORE: initial[m1.CORE]}, {m1.CORE: after[m1.CORE]})
        if command == 'recovery-check':
            raise RecoveryExercise('Intentional post-apply failure for recovery verification')
        final = m1.baseline(out, 'after')
        m1.assert_steady(after, final)
        if previous == target and command != 'rebuild':
            m1.assert_steady(initial, final)
        return {'previousState': previous, 'finalState': target, 'idempotent': previous == target and command != 'rebuild',
                'controlledRebuild': command == 'rebuild', 'recoveryExercised': False}
    except Exception as exc:
        if not changed:
            raise
        recovery = {'trigger': str(exc), 'intentional': isinstance(exc, RecoveryExercise), 'outcome': 'failed'}
        try:
            apply_fixed(out / 'rollback', previous)
            _, restored, _ = verify(out / 'live-restored', previous)
            m1.assert_steady({m1.CORE: initial[m1.CORE]}, {m1.CORE: restored[m1.CORE]})
            checked = m1.baseline(out, 'after-rollback')
            m1.assert_steady(restored, checked)
            recovery['outcome'] = 'passed'
        finally:
            m1.save(out, 'recovery.json', recovery)
        if not isinstance(exc, RecoveryExercise):
            raise
        return {'previousState': previous, 'attemptedState': target, 'finalState': previous,
                'recoveryExercised': True, 'controlledFailureObserved': True}


def measure_protected(out, initial, topology):
    spec, _, routing, _, _, policies, probes = np.render()
    target, envoy_name, envoy, backends, services = topology
    m1.save(out, 'probes.yaml', yaml.safe_dump_all(probes, sort_keys=False))
    m1.save(out, 'probes-dry-run.txt', m1.kube('apply', '--dry-run=server', '-f', out / 'probes.yaml').stdout)
    created = False
    try:
        created = True
        m1.save(out, 'probes-apply.txt', m1.kube('apply', '-f', out / 'probes.yaml').stdout)
        for probe in probes:
            m1.kube('-n', m1.NS, 'wait', 'pod/' + probe['metadata']['name'], '--for=condition=Ready', '--timeout=90s', timeout=100)
        state = m1.snapshot()
        stripped = copy.deepcopy(state)
        for probe in probes:
            name = probe['metadata']['name']
            m1.verify_image_lock(state[m1.NS][name], m1.config()[1]['images']['backend']['image'])
            stripped[m1.NS].pop(name)
        m1.assert_steady(initial, stripped)
        locked = np.runtime()
        m1.save(out, 'measurement-pods.json', state)
        m1.save(out, 'runtime-before.json', locked)
        np.policy_state(out, 'policies-before-measure', policies, True)
        checks = np.matrix(out / 'isolation', spec, state, locked, envoy_name, backends, services, True, spec['isolationRounds'])
        rows = np.business(out / 'business', spec, state, locked, target, envoy_name, envoy, backends, services, routing)
        np.steady(state, locked)
        m1.save(out, 'runtime-after.json', np.runtime())
        np.policy_state(out, 'policies-after-measure', policies, True)
        return {'sessions': len(rows), 'proxyConnections': sum(r['proxyConnections'] for r in rows),
                'allowedChecks': sum(r['expectedAllowed'] for r in checks),
                'deniedChecks': sum(not r['expectedAllowed'] for r in checks), 'fullAcceptance': True}
    finally:
        if created:
            m1.save(out, 'probes-cleanup.txt', m1.kube('delete', '-f', out / 'probes.yaml', '--ignore-not-found=true',
                                                    '--wait=true', timeout=60).stdout)


def measure(out):
    before = m1.baseline(out, 'before')
    current, state, topology = verify(out / 'live-before')
    m1.assert_steady(before, state)
    m1.save(out, 'manifest.yaml', yaml.safe_dump_all(render(current), sort_keys=False))
    folder = out / 'measurement'
    folder.mkdir()
    if current == 'qos':
        spec, routing, images, docs = m3.render()
        result = m3.measure(folder, spec, routing, images, docs)
    elif current in ['neutral', 'placed']:
        spec, routing, images, docs = m4.render(current == 'placed')
        result = m4.measure(folder, spec, routing, images, docs, current == 'placed', False)
        result['sourceFullAcceptance'] = result['fullAcceptance']
        result['fullAcceptance'] = result['sessions'] == 4 * spec['roundsPerClassPerDirection']
    else:
        result = measure_protected(folder, state, topology)
    final = m1.baseline(out, 'after')
    m1.assert_steady(before, final)
    verify(out / 'live-after', current)
    return {'state': current, **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['render', 'check', 'apply', 'snapshot', 'restore', 'rebuild', 'recovery-check', 'measure'])
    parser.add_argument('--target', choices=['neutral', 'qos', 'placed', 'protected'])
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    spec = profile()
    if args.command == 'restore':
        if not args.snapshot:
            parser.error('restore requires --snapshot from a previous checked state')
        target = validate_snapshot(json.loads(args.snapshot.read_text(encoding='utf-8')))
    else:
        target = args.target or spec['defaultState']
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    inputs = [PROFILE, Path(__file__), *[ROOT / p for p in spec['inputSha256LF']]]
    m1.save(out, 'metadata.json', {'route': 'R2', 'stage': 'R2.S3', 'configVersion': spec['configVersion'],
        'command': args.command, 'requestedTarget': target if args.command in
            ['render', 'apply', 'restore', 'rebuild', 'recovery-check'] else None,
        'startedAt': m1.now(), 'evidencePath': str(out),
        'sha256LF': {str(p.relative_to(ROOT)): m2.sha(p) for p in inputs}})
    m1.save(out, 'profile.json', spec)
    if args.command in ['render', 'apply', 'restore', 'rebuild', 'recovery-check']:
        m1.save(out, 'manifest.yaml', yaml.safe_dump_all(render(target), sort_keys=False))
    result = {'outcome': 'failed', 'command': args.command}
    try:
        if args.command in ['apply', 'restore', 'rebuild', 'recovery-check']:
            result['evidence'] = transition(out, target, args.command)
        elif args.command in ['check', 'snapshot']:
            baseline = m1.baseline(out, 'baseline')
            current, state, _ = verify(out / 'live')
            m1.assert_steady(baseline, state)
            m1.save(out, 'snapshot.json', snapshot(current, state))
            m1.save(out, 'manifest.yaml', yaml.safe_dump_all(render(current), sort_keys=False))
            result['evidence'] = {'state': current}
        elif args.command == 'measure':
            result['evidence'] = measure(out)
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
        print(f'CNF profile ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
