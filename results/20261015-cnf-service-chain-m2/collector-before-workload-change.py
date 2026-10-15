#!/usr/bin/env python3
"""Fixed R2.S2 M2 experiment extending the frozen M1 baseline."""
import argparse
import copy
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / 'infra/cnfs/m2-experiment.json'
module = importlib.util.spec_from_file_location('cnf_m1', ROOT / 'scripts/cnf-m1.py')
m1 = importlib.util.module_from_spec(module)
module.loader.exec_module(m1)


def sha(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def render():
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    original, images = m1.config()
    docs = copy.deepcopy(original)
    by_name = {(d['kind'], d['metadata']['name']): d for d in docs}
    cm = by_name['ConfigMap', 'cnf-m1-envoy']
    cm['metadata']['name'] = spec['configMap']
    cm['metadata']['labels']['app.kubernetes.io/part-of'] = 'cnf-m2'
    config = yaml.safe_load(cm['data']['envoy.yaml'])
    listener = config['static_resources']['listeners'][0]
    cluster = config['static_resources']['clusters'][0]
    listeners, clusters = [], []
    for route in spec['classes']:
        item = copy.deepcopy(listener)
        item['name'] = route['listener']
        item['address']['socket_address']['port_value'] = route['listenPort']
        proxy = item['filter_chains'][0]['filters'][0]['typed_config']
        proxy['cluster'] = route['cluster']
        proxy['stat_prefix'] = route['listener']
        log = proxy['access_log'][0]['typed_config']['log_format']['json_format']
        log.update(experiment=spec['configVersion'], listener=route['listener'], business_class=route['name'])
        listeners.append(item)
        item = copy.deepcopy(cluster)
        item['name'] = route['cluster']
        assignment = item['load_assignment']
        assignment['cluster_name'] = route['cluster']
        assignment['endpoints'][0]['lb_endpoints'][0]['endpoint']['address']['socket_address']['address'] = (
            route['backendService'] + '.cnf-lab.svc.cluster.local.')
        clusters.append(item)
    config['static_resources'].update(listeners=listeners, clusters=clusters)
    cm['data']['envoy.yaml'] = yaml.safe_dump(config, sort_keys=False)
    gateway = by_name['Deployment', spec['gatewayDeployment']]
    pod = gateway['spec']['template']
    pod.setdefault('metadata', {}).setdefault('annotations', {})['cnf.lab/config-sha256'] = hashlib.sha256(
        cm['data']['envoy.yaml'].encode()).hexdigest()
    pod['spec']['volumes'][0]['configMap']['name'] = spec['configMap']
    pod['spec']['containers'][0]['ports'] = [dict(name=r['name'], containerPort=r['listenPort'], protocol='TCP')
                                            for r in spec['classes']]
    service = by_name['Service', spec['gatewayService']]
    service['spec']['ports'] = [dict(name=r['name'], port=r['listenPort'], targetPort=r['name'],
                                    nodePort=r['nodePort'], protocol='TCP') for r in spec['classes']]
    edge = copy.deepcopy(by_name['Deployment', 'cnf-m1-backend'])
    edge['metadata'].update(name='cnf-m2-edge', labels={'app.kubernetes.io/part-of': 'cnf-m2'})
    edge['spec']['selector']['matchLabels'] = {'app': 'cnf-m2-edge'}
    edge['spec']['template']['metadata']['labels'] = {'app': 'cnf-m2-edge', 'app.kubernetes.io/part-of': 'cnf-m2'}
    edge_service = copy.deepcopy(by_name['Service', 'cnf-m1-backend'])
    edge_service['metadata'].update(name='cnf-m2-edge', labels={'app.kubernetes.io/part-of': 'cnf-m2'})
    edge_service['spec']['selector'] = {'app': 'cnf-m2-edge'}
    # Do not re-apply the existing core backend, its Service, or the Namespace.
    return spec, images, [cm, edge, edge_service, gateway, service]


def verify_live(state, spec, images, docs):
    node, target = m1.target_node()
    envoy_name, envoy = m1.one_pod(state, m1.NS, spec['gatewayDeployment'])
    m1.verify_image_lock(envoy, images['images']['envoy']['image'])
    backend = {}
    for route in spec['classes']:
        name, pod = m1.one_pod(state, m1.NS, route['backendDeployment'])
        m1.verify_image_lock(pod, images['images']['backend']['image'])
        backend[route['name']] = (name, pod)
    if any(pod['node'] != node['metadata']['name'] for _, pod in [(envoy_name, envoy), *backend.values()]):
        raise RuntimeError('M2 requires Envoy and both backends on the control-role node')
    for route in spec['classes']:
        deployment = json.loads(m1.kube('-n', m1.NS, 'get', 'deployment', route['backendDeployment'], '-o', 'json').stdout)
        live = deployment['spec']['template']['spec']
        expected = next(d for d in m1.config()[0] if d['kind'] == 'Deployment' and d['metadata']['name'] == 'cnf-m1-backend')['spec']['template']['spec']
        for field in ['nodeSelector', 'securityContext', 'volumes']:
            if live[field] != expected[field]:
                raise RuntimeError('Backend placement/security settings differ from the shared baseline')
        container = live['containers'][0]
        if container['resources'] != expected['containers'][0]['resources']:
            raise RuntimeError('M2 backends must have identical resource requests/limits')
    cm = json.loads(m1.kube('-n', m1.NS, 'get', 'configmap', spec['configMap'], '-o', 'json').stdout)
    expected_cm = next(d for d in docs if d['kind'] == 'ConfigMap')
    if cm['data'] != expected_cm['data']:
        raise RuntimeError('Live M2 Envoy ConfigMap differs from the recorded configuration')
    services = {s['metadata']['name']: s for s in json.loads(m1.kube('-n', m1.NS, 'get', 'services', '-o', 'json').stdout)['items']}
    gateway = services[spec['gatewayService']]
    actual_ports = {p['nodePort']: p['port'] for p in gateway['spec']['ports']}
    if gateway['spec'].get('externalTrafficPolicy') != 'Local' or actual_ports != {
            r['nodePort']: r['listenPort'] for r in spec['classes']}:
        raise RuntimeError('M2 NodePort mapping differs from the fixed experiment')
    endpoints = {s['metadata']['name']: s for s in json.loads(m1.kube('-n', m1.NS, 'get', 'endpoints', '-o', 'json').stdout)['items']}
    for route in spec['classes']:
        service = services[route['backendService']]
        addresses = [a['ip'] for part in endpoints[route['backendService']].get('subsets', []) for a in part.get('addresses', [])]
        if service['spec']['type'] != 'ClusterIP' or addresses != [backend[route['name']][1]['ip']]:
            raise RuntimeError('Backend Service does not map to its single expected Pod')
    return target, envoy_name, envoy, backend, services


def deploy(out, spec, images, docs):
    before = m1.baseline(out, 'before-deploy')
    # Only extend the previously inspected M1 namespace, and reject colliding new names/ports.
    cm = json.loads(m1.kube('-n', m1.NS, 'get', 'configmap', 'cnf-m1-envoy', '-o', 'json').stdout)
    if cm['data'] != next(d['data'] for d in m1.config()[0] if d['kind'] == 'ConfigMap'):
        raise RuntimeError('The existing M1 configuration is not the verified baseline')
    for resource in ['configmap/cnf-m2-envoy', 'deployment/cnf-m2-edge', 'service/cnf-m2-edge']:
        if m1.kube('-n', m1.NS, 'get', resource, '--ignore-not-found', '-o', 'name').stdout.strip():
            raise RuntimeError('An M2 resource already exists; inspect it before another apply')
    services = json.loads(m1.kube('get', 'services', '-A', '-o', 'json').stdout)['items']
    if any(p.get('nodePort') == 31202 for s in services for p in s['spec'].get('ports', [])):
        raise RuntimeError('NodePort 31202 is occupied')
    manifest = out / 'manifest.yaml'
    m1.save(out, 'server-dry-run.txt', m1.kube('apply', '--dry-run=server', '-f', manifest).stdout)
    m1.save(out, 'apply.txt', m1.kube('apply', '-f', manifest).stdout)
    for name in ['cnf-m2-edge', spec['gatewayDeployment']]:
        m1.save(out, name + '-rollout.txt', m1.kube('-n', m1.NS, 'rollout', 'status', 'deployment/' + name,
                                                 '--timeout=120s', timeout=130).stdout)
    after = m1.baseline(out, 'after-deploy')
    m1.assert_steady({m1.CORE: before[m1.CORE]}, {m1.CORE: after[m1.CORE]})
    name, old = m1.one_pod(before, m1.NS, 'cnf-m1-backend')
    m1.assert_steady({m1.NS: {name: old}}, {m1.NS: {name: after[m1.NS][name]}})
    verify_live(after, spec, images, docs)


def parse_time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def validate_round(data, logs, correct_backend, other_backend, route, spec, service_ip, envoy_ip, started, ended):
    end = data.get('end', {})
    sent = end.get('sum_sent', {}).get('bytes', 0)
    received = end.get('sum_received', {}).get('bytes', 0)
    cookie = data.get('start', {}).get('cookie')
    if data.get('error') or sent != spec['payloadBytes'] or received != sent or not cookie:
        raise RuntimeError('iperf did not transfer the exact per-session payload')
    entries = []
    for line in logs.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get('experiment') == spec['configVersion'] and 'start' in item:
            if parse_time(started) <= parse_time(item['start']) <= parse_time(ended):
                entries.append(item)
    if any(x.get('business_class') != route['name'] or x.get('listener') != route['listener'] or
           x.get('cluster') != route['cluster'] or x.get('upstream') != service_ip + ':5201' or
           x.get('local', '').rsplit(':', 1)[-1] != str(route['listenPort']) or x.get('flags') != '-'
           for x in entries):
        raise RuntimeError('Wrong class, listener, upstream or failed proxy connection observed')
    if len(entries) < 2:
        raise m1.PendingProxyLogs('Waiting for both M2 control/data proxy records')
    if len(entries) != 2 or not any(int(x.get('bytes_received') or 0) >= received for x in entries):
        raise RuntimeError('Unexpected proxy connection count or missing payload bytes')
    if cookie in other_backend:
        raise RuntimeError('The other backend received the current session cookie')
    if cookie not in correct_backend or not re.search(r'Accepted connection from ' + re.escape(envoy_ip) + r'[,\s]', correct_backend):
        raise RuntimeError('Expected backend cookie/proxy peer evidence is missing')
    return {'cookie': cookie, 'receivedBytes': received, 'proxyConnections': len(entries),
            'backendHit': route['name'], 'incorrectRoutes': 0}


def transfer(out, ue, upf, state, command):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 25
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
                if memory >= 512 * 1024 ** 2 or time.monotonic() >= deadline:
                    raise RuntimeError('Memory stop threshold or session deadline exceeded')
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
        raise RuntimeError('iperf failed; retained raw output')
    return json.loads(stdout)


def measure(out, spec, images, docs, count):
    state = m1.baseline(out, 'before-measure')
    target, envoy_name, envoy, backends, services = verify_live(state, spec, images, docs)
    ue, source = m1.ue_path(out, state, target)
    upf, _ = m1.one_pod(state, m1.CORE, 'free5gc-helm-free5gc-upf-upf')
    if m1.kube('-n', m1.CORE, 'exec', ue, '--', 'pgrep', '-x', 'iperf3', required=False).returncode != 1:
        raise RuntimeError('UE is already running iperf or process inspection failed')
    m1.save(out, 'services.json', services)
    results = []
    m1.save(out, 'rounds.json', results)
    try:
        for number in range(1, count + 1):
            for route in spec['classes']:
                folder = out / f'{number:02d}-{route["name"]}'
                folder.mkdir()
                row = {'round': number, 'businessClass': route['name'], 'nodePort': route['nodePort'], 'outcome': 'failed'}
                try:
                    m1.assert_steady(state, m1.snapshot())
                    first = m1.upf_observation(folder, 'before', upf)
                    started = m1.now()
                    command = ['kubectl', '-n', m1.CORE, 'exec', ue, '--', 'iperf3', '-c', target,
                               '-B', source, '-p', str(route['nodePort']), '-P', '1',
                               '-b', str(spec['offeredMbps']) + 'M', '-n', str(spec['payloadBytes']),
                               '-l', str(spec['payloadBytes']), '-J', '--get-server-output', '--connect-timeout', '5000']
                    m1.save(folder, 'command.json', command)
                    data = transfer(folder, ue, upf, state, command)
                    ended = m1.now()
                    row.update(startedAt=started, trafficEndedAt=ended)
                    last = m1.upf_observation(folder, 'after', upf)
                    if not all(b > a for a, b in zip(m1.path_counters(first['interfaces']), m1.path_counters(last['interfaces']))):
                        raise RuntimeError('UPF did not record bidirectional traffic in this session')
                    other = next(r for r in spec['classes'] if r['name'] != route['name'])
                    wait_started = time.monotonic()
                    while True:
                        logs = m1.kube('-n', m1.NS, 'logs', envoy_name, '--since-time=' + started).stdout
                        correct = m1.kube('-n', m1.NS, 'logs', backends[route['name']][0], '--since-time=' + started).stdout
                        wrong = m1.kube('-n', m1.NS, 'logs', backends[other['name']][0], '--since-time=' + started).stdout
                        m1.save(folder, 'envoy-access.txt', logs)
                        m1.save(folder, 'expected-backend.txt', correct)
                        m1.save(folder, 'other-backend.txt', wrong)
                        try:
                            evidence = validate_round(data, logs, correct, wrong, route, spec,
                                                       services[route['backendService']]['spec']['clusterIP'],
                                                       envoy['ip'], started, ended)
                            break
                        except m1.PendingProxyLogs:
                            if time.monotonic() - wait_started >= 15:
                                raise
                            m1.assert_steady(state, m1.snapshot())
                            time.sleep(0.25)
                    m1.assert_steady(state, m1.snapshot())
                    row.update(evidence, outcome='passed', logWaitSeconds=round(time.monotonic() - wait_started, 3))
                    print(f'{number:02d}/{count:02d} {route["name"]}: passed, {evidence["receivedBytes"]} bytes, 2 proxy connections', flush=True)
                except Exception as exc:
                    row['error'] = str(exc)
                    raise
                finally:
                    results.append(row)
                    m1.save(folder, 'summary.json', row)
                    m1.save(out, 'rounds.json', results)
    finally:
        # Preserve a final status even if an individual flow fails; do not restart anything.
        m1.save(out, 'final-pods.json', m1.snapshot())
    final = m1.baseline(out, 'after-measure')
    m1.assert_steady(state, final)
    return {'classes': {r['name']: {'correct': sum(x['outcome'] == 'passed' and x['businessClass'] == r['name'] for x in results),
                                   'required': count, 'incorrectRoutes': 0} for r in spec['classes']},
            'sessions': len(results), 'proxyConnections': sum(x['proxyConnections'] for x in results),
            'fullAcceptance': count == spec['sessionsPerClass']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['render', 'deploy', 'measure'])
    parser.add_argument('--out', required=True, help='New unique result directory')
    parser.add_argument('--smoke', action='store_true', help='One session per class, never full M2 acceptance')
    args = parser.parse_args()
    spec, images, docs = render()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    inputs = [SPEC, m1.MANIFEST, m1.LOCK, ROOT / 'scripts/cnf-m1.py', Path(__file__),
              ROOT / 'scripts/free5gc-profile.py', ROOT / 'infra/profiles/free5gc-3node-no-multus-v1.json']
    m1.save(out, 'metadata.json', {**{k: spec[k] for k in ['route', 'stage', 'milestone', 'configVersion']},
                                  'startedAt': m1.now(), 'command': args.command, 'smoke': args.smoke,
                                  'evidencePath': str(out), 'sha256LF': {str(p.relative_to(ROOT)): sha(p) for p in inputs}})
    m1.save(out, 'experiment.json', spec)
    m1.save(out, 'images.json', images)
    m1.save(out, 'manifest.yaml', yaml.safe_dump_all(docs, sort_keys=False))
    m1.save(out, 'envoy.yaml', docs[0]['data']['envoy.yaml'])
    result = {'outcome': 'failed', 'command': args.command}
    try:
        if args.command == 'deploy':
            deploy(out, spec, images, docs)
        elif args.command == 'measure':
            result['evidence'] = measure(out, spec, images, docs, 1 if args.smoke else spec['sessionsPerClass'])
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
        print(f'CNF M2 ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
