#!/usr/bin/env python3
"""R2.S4/A: two fixed UEs and a separate, protected, low-load TCP fixture."""
import argparse
import copy
import importlib.util
import ipaddress
import json
from pathlib import Path
import subprocess
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / 'infra/cnfs/multi-ue-experiment.json'
module = importlib.util.spec_from_file_location('cnf_profile', ROOT / 'scripts/cnf-profile.py')
base = importlib.util.module_from_spec(module)
module.loader.exec_module(base)
m1, m2, m3, m4 = base.m1, base.m2, base.m3, base.m4
NS = 'cnf-multi-ue'
PROXY = 'cnf-a-envoy'
VERSION = 'cnf-multi-ue-v1'


def require(value, message):
    if not value:
        raise RuntimeError(message)


def config():
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    require(spec['configVersion'] == VERSION and spec['namespace'] == NS and
            spec['baseProfile'] == base.profile()['configVersion'] and spec['baseState'] == 'protected',
            'Unexpected fixed experiment/profile')
    require([(u['name'], u['supi'], u['deployment']) for u in spec['users']] == [
        ('ue1', 'imsi-208930000000001', 'ueransim-ue'),
        ('ue2', 'imsi-208930000000002', 'cnf-a-ue2')], 'UE identities differ from fixed test')
    require([(r['user'], r['businessClass'], r['listenPort'], r['nodePort']) for r in spec['routes']] == [
        ('ue1', 'core', 15301, 31301), ('ue2', 'core', 15302, 31302),
        ('ue1', 'edge', 15303, 31303), ('ue2', 'edge', 15304, 31304)], 'Unexpected route/port mapping')
    require((spec['durationSeconds'], spec['offeredMbpsPerUE'], spec['roundsPerClassPerDirection'],
             spec['minimumOverlapSeconds'], spec['maximumUpfMemoryBytes']) == (10, 1, 5, 8, 512 * 1024 ** 2),
            'Load/guard changes require a reviewed experiment version')
    require(spec['ue2Subscriber'] == 'infra/free5gc-test-subscriber-ue2.json', 'Unexpected subscriber path')
    subscriber = json.loads((ROOT / spec['ue2Subscriber']).read_text(encoding='utf-8'))
    require(subscriber['ueId'] == spec['users'][1]['supi'] and subscriber['plmnID'] == '20893' and
            subscriber['FlowRules'] == [] and subscriber['QosFlows'] == [], 'Unexpected UE2 identity or QoS')
    return spec, subscriber


def route_name(route):
    return 'cnf-a-' + route['businessClass'] + '-' + route['user']


def render():
    spec, subscriber = config()
    original = m1.config()[0]
    labels = {'app.kubernetes.io/part-of': VERSION}
    def obj(kind, name, body, namespace=NS):
        return {'apiVersion': 'apps/v1' if kind == 'Deployment' else
                'networking.k8s.io/v1' if kind == 'NetworkPolicy' else 'v1', 'kind': kind,
                'metadata': {'name': name, 'namespace': namespace, 'labels': dict(labels)},
                'data' if kind == 'ConfigMap' else 'spec': body}
    docs = [{'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NS, 'labels': dict(labels)}}]
    backend = next(d for d in original if d['kind'] == 'Deployment' and d['metadata']['name'] == 'cnf-m1-backend')
    proxy = next(d for d in original if d['kind'] == 'Deployment' and d['metadata']['name'] == 'cnf-m1-envoy')
    envoy = yaml.safe_load(next(d for d in original if d['kind'] == 'ConfigMap')['data']['envoy.yaml'])
    listener, cluster = envoy['static_resources']['listeners'][0], envoy['static_resources']['clusters'][0]
    listeners, clusters = [], []
    ports = []
    for route in spec['routes']:
        name = route_name(route)
        body = copy.deepcopy(backend['spec'])
        body['selector']['matchLabels'] = {'app': name}
        body['template']['metadata']['labels'] = {'app': name, 'role': 'backend', **labels}
        body['template']['spec']['nodeSelector'] = {'5gc.free5gc.org/plane': 'control'} if route['businessClass'] == 'core' else {
            '5gc.free5gc.org/role': 'ran-test'}
        docs += [obj('Deployment', name, body), obj('Service', name, {
            'selector': {'app': name}, 'ports': [{'name': 'iperf', 'port': 5201, 'targetPort': 'iperf', 'protocol': 'TCP'}]})]
        item = copy.deepcopy(listener)
        item['name'] = name
        item['address']['socket_address']['port_value'] = route['listenPort']
        tcp = item['filter_chains'][0]['filters'][0]['typed_config']
        tcp.update(cluster=name, stat_prefix=name)
        tcp['access_log'][0]['typed_config']['log_format']['json_format'].update(
            experiment=VERSION, listener=name, business_class=route['businessClass'], test_user=route['user'])
        listeners.append(item)
        item = copy.deepcopy(cluster)
        item['name'] = item['load_assignment']['cluster_name'] = name
        item['load_assignment']['endpoints'][0]['lb_endpoints'][0]['endpoint']['address']['socket_address']['address'] = (
            name + '.' + NS + '.svc.cluster.local.')
        clusters.append(item)
        ports.append({'name': route['user'] + '-' + route['businessClass'], 'port': route['listenPort'],
                      'targetPort': route['listenPort'], 'nodePort': route['nodePort'], 'protocol': 'TCP'})
    envoy['static_resources'].update(listeners=listeners, clusters=clusters)
    body = copy.deepcopy(proxy['spec'])
    body['selector']['matchLabels'] = {'app': PROXY}
    body['template']['metadata']['labels'] = {'app': PROXY, **labels}
    body['template']['spec']['volumes'][0]['configMap']['name'] = PROXY
    body['template']['spec']['containers'][0]['ports'] = [
        {'name': p['name'], 'containerPort': p['port'], 'protocol': 'TCP'} for p in ports]
    docs += [obj('ConfigMap', PROXY, {'envoy.yaml': yaml.safe_dump(envoy, sort_keys=False)}),
             obj('Deployment', PROXY, body), obj('Service', PROXY, {
                 'type': 'NodePort', 'externalTrafficPolicy': 'Local', 'selector': {'app': PROXY}, 'ports': ports})]
    docs += [obj('NetworkPolicy', 'cnf-a-backends', {'podSelector': {'matchLabels': {'role': 'backend'}},
        'policyTypes': ['Ingress'], 'ingress': [{'from': [{'podSelector': {'matchLabels': {'app': PROXY}}}],
                                               'ports': [{'protocol': 'TCP', 'port': 5201}]}]}),
        obj('NetworkPolicy', 'cnf-a-envoy', {'podSelector': {'matchLabels': {'app': PROXY}},
        'policyTypes': ['Ingress', 'Egress'], 'ingress': [{'ports': [
            {'protocol': 'TCP', 'port': r['listenPort']} for r in spec['routes']]}], 'egress': [
            {'to': [{'podSelector': {'matchLabels': {'role': 'backend'}}}], 'ports': [{'protocol': 'TCP', 'port': 5201}]},
            {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}},
                     'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}],
             'ports': [{'protocol': p, 'port': 53} for p in ['UDP', 'TCP']]}]})]
    auth = subscriber['AuthenticationSubscription']
    slice_id = {'sst': 1, 'sd': 0x010203}
    ue = {'supi': subscriber['ueId'], 'mcc': '208', 'mnc': '93',
          'key': auth['permanentKey']['permanentKeyValue'], 'op': auth['opc']['opcValue'], 'opType': 'OPC',
          'amf': auth['authenticationManagementField'], 'imei': '356938035643804', 'imeiSv': '4370816125816152',
          'uacAic': {'mps': False, 'mcs': False}, 'uacAcc': {'normalClass': 0, **{f'class{x}': False for x in range(11, 16)}},
          'sessions': [{'type': 'IPv4', 'apn': 'internet', 'slice': slice_id}],
          'configured-nssai': [slice_id], 'default-nssai': [{'sst': 1, 'sd': 1}],
          'integrity': {f'IA{x}': True for x in [1, 2, 3]}, 'ciphering': {f'EA{x}': True for x in [1, 2, 3]},
          'integrityMaxRate': {'uplink': 'full', 'downlink': 'full'}, 'gnbSearchList': ['gnb-service']}
    wrapper = '#!/bin/sh\nset -eu\nmkdir -p /dev/net\n[ -c /dev/net/tun ] || mknod /dev/net/tun c 10 200\nexec ./nr-ue -c /ueransim/config/ue-config.yaml\n'
    name = spec['users'][1]['deployment']
    docs += [obj('ConfigMap', name, {'ue-config.yaml': yaml.safe_dump(ue, sort_keys=False), 'wrapper.sh': wrapper}, m1.CORE),
             obj('Deployment', name, {'replicas': 1, 'strategy': {'type': 'Recreate'},
                'selector': {'matchLabels': {'app': name}}, 'template': {'metadata': {'labels': {'app': name, **labels}},
                'spec': {'automountServiceAccountToken': False, 'nodeSelector': {'5gc.free5gc.org/role': 'ran-test'},
                    'containers': [{'name': 'ue', 'image': m1.config()[1]['images']['backend']['image'], 'imagePullPolicy': 'Never',
                        'command': ['/bin/sh', '/ueransim/config/wrapper.sh'],
                        'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'}, 'limits': {'cpu': '500m', 'memory': '256Mi'}},
                        'securityContext': {'capabilities': {'add': ['NET_ADMIN']}},
                        'readinessProbe': {'exec': {'command': ['sh', '-ec', 'test -d /sys/class/net/uesimtun0 && kill -0 1']},
                                           'initialDelaySeconds': 2, 'periodSeconds': 2},
                        'volumeMounts': [{'name': 'ue', 'mountPath': '/ueransim/config', 'readOnly': True}]}],
                    'volumes': [{'name': 'ue', 'configMap': {'name': name}}]}}}, m1.CORE)]
    return spec, docs


def snapshot():
    state = m1.snapshot()
    result = m1.kube('-n', NS, 'get', 'pods', '-o', 'json', required=False)
    state[NS] = {}
    if result.returncode:
        require('NotFound' in result.stderr, 'Cannot inspect experiment namespace')
        return state
    for pod in json.loads(result.stdout)['items']:
        require(not pod['metadata'].get('deletionTimestamp'), 'A fixture Pod is terminating')
        state[NS][pod['metadata']['name']] = {'uid': pod['metadata']['uid'], 'node': pod['spec'].get('nodeName'),
            'ip': pod['status'].get('podIP'), 'phase': pod['status']['phase'], 'containers': [
                {key: c.get(key) for key in ['name', 'image', 'imageID', 'containerID', 'ready', 'restartCount', 'state', 'lastState']} |
                {'specImage': next(x['image'] for x in pod['spec']['containers'] if x['name'] == c['name'])}
                for c in pod['status'].get('containerStatuses', [])]}
    return state


def baseline(out):
    initial = m1.baseline(out, 'original')
    base.verify(out / 'original-profile', 'protected')
    return initial


def same_original(before, after):
    selected = {ns: {n: after[ns][n] for n in pods} for ns, pods in before.items()}
    m1.assert_steady(before, selected)


def verify_fixture(out):
    out.mkdir()
    spec, docs = render()
    initial = baseline(out)
    state = snapshot()
    m1.assert_steady(state, state)
    require(len(state[m1.CORE]) == 16 and len(state[m1.NS]) == 3 and len(state[NS]) == 5,
            'Unexpected original/UE/fixture Pod count')
    objects = json.loads(m1.kube('-n', NS, 'get',
        'deployments,services,configmaps,networkpolicies,statefulsets,daemonsets,jobs,cronjobs', '-o', 'json').stdout)['items']
    objects = [o for o in objects if (o['kind'], o['metadata']['name']) != ('ConfigMap', 'kube-root-ca.crt')]
    expected = [d for d in docs if d['metadata'].get('namespace') == NS]
    require({(o['kind'], o['metadata']['name']) for o in objects} ==
            {(o['kind'], o['metadata']['name']) for o in expected}, 'Fixture resource set differs')
    for desired in [*expected, *[d for d in docs if d['metadata'].get('namespace') == m1.CORE]]:
        actual = json.loads(m1.kube('-n', desired['metadata']['namespace'], 'get', desired['kind'],
                                    desired['metadata']['name'], '-o', 'json').stdout)
        field = 'data' if desired['kind'] == 'ConfigMap' else 'spec'
        m3.require_subset(desired[field], actual[field])
        if desired['kind'] in ['ConfigMap', 'NetworkPolicy']:
            require(desired[field] == actual[field], 'Unexpected configuration/policy field')
    control, target = m1.target_node()
    locks = m1.config()[1]['images']
    backend_info, services = {}, {}
    envoy, proxy = m1.one_pod(state, NS, PROXY)
    m1.verify_image_lock(proxy, locks['envoy']['image'])
    require(proxy['node'] == control['metadata']['name'], 'Proxy on wrong node')
    for route in spec['routes']:
        name = route_name(route)
        pod_name, pod = m1.one_pod(state, NS, name)
        m1.verify_image_lock(pod, locks['backend']['image'])
        require(pod['node'] == (control['metadata']['name'] if route['businessClass'] == 'core' else 'ran-test'),
                'Backend on wrong node')
        svc = json.loads(m1.kube('-n', NS, 'get', 'service', name, '-o', 'json').stdout)
        endpoint = json.loads(m1.kube('-n', NS, 'get', 'endpoints', name, '-o', 'json').stdout)
        slices = json.loads(m1.kube('-n', NS, 'get', 'endpointslices', '-l', 'kubernetes.io/service-name=' + name,
                                    '-o', 'json').stdout)['items']
        m4.validate_endpoint(endpoint, slices, pod_name, pod, pod['node'])
        m1.save(out, name + '-endpoints.json', {'endpoints': endpoint, 'slices': slices})
        qdisc = json.loads(m1.kube('-n', NS, 'exec', pod_name, '--', 'tc', '-j', '-s', 'qdisc', 'show', 'dev', 'eth0').stdout)
        m4.no_shaping(qdisc)
        m1.save(out, name + '-qdisc.json', qdisc)
        services[name] = svc
        backend_info[name] = {'pod': pod_name, **pod}
    user_info = {}
    for user in spec['users']:
        name, pod = m1.one_pod(state, m1.CORE, user['deployment'])
        m1.verify_image_lock(pod, locks['backend']['image'])
        require(pod['node'] == 'ran-test', 'UE on wrong node')
        ids = m1.kube('-n', m1.CORE, 'exec', name, '--', './nr-cli', '-d').stdout.strip().splitlines()
        require(ids == [user['supi']], 'UE runtime SUPI differs')
        status = m1.kube('-n', m1.CORE, 'exec', name, '--', './nr-cli', user['supi'], '-e', 'status').stdout
        sessions = m1.kube('-n', m1.CORE, 'exec', name, '--', './nr-cli', user['supi'], '-e', 'ps-list').stdout
        log = m1.kube('-n', m1.CORE, 'logs', name, '--tail=400').stdout
        require('rm-state: RM-REGISTERED' in status and 'state: PS-ACTIVE' in sessions and
                all(s in log for s in ['Authentication Request received', 'Security Mode Command received',
                                      'Initial Registration is successful', 'PDU Session establishment is successful']),
                'UE registration/authentication/PDU evidence missing')
        address = json.loads(m1.kube('-n', m1.CORE, 'exec', name, '--', 'ip', '-j', '-4', 'addr', 'show', 'uesimtun0').stdout)
        ips = [a['local'] for item in address for a in item['addr_info'] if a['family'] == 'inet']
        require(len(ips) == 1 and 'address: ' + ips[0] in sessions, 'UE runtime/session IP differs')
        ipaddress.IPv4Address(ips[0])
        route = m1.kube('-n', m1.CORE, 'exec', name, '--', 'ip', '-4', 'route', 'get', target, 'from', ips[0]).stdout
        require('dev uesimtun0' in route, 'UE route bypasses tunnel')
        ping = m1.kube('-n', m1.CORE, 'exec', name, '--', 'ping', '-I', 'uesimtun0', '-c', '3', '-W', '2', target).stdout
        require('0% packet loss' in ping and '100% packet loss' not in ping, 'UE tunnel ping failed')
        require(m1.kube('-n', m1.CORE, 'exec', name, '--', 'pgrep', '-x', 'iperf3', required=False).returncode == 1,
                'UE already runs iperf or inspection failed')
        for suffix, data in [('status.txt', status), ('sessions.txt', sessions), ('registration.txt', log),
                             ('address.json', address), ('route.txt', route), ('ping.txt', ping)]:
            m1.save(out, user['name'] + '-' + suffix, data)
        user_info[user['name']] = {**user, 'pod': name, 'podUID': pod['uid'], 'ip': ips[0], 'node': pod['node']}
    require(len({u['ip'] for u in user_info.values()}) == 2, 'UE IP addresses overlap')
    same_original(initial, state)
    topology = {'target': target, 'envoy': {'pod': envoy, **proxy}, 'users': user_info,
                'backends': backend_info, 'services': services}
    m1.save(out, 'topology.json', topology)
    m1.save(out, 'pods.json', state)
    m1.save(out, 'resources.json', [{'kind': o['kind'], 'name': o['metadata']['name'], 'uid': o['metadata']['uid'],
        'body': o.get('data') if o['kind'] == 'ConfigMap' else o['spec']} for o in objects])
    return state, topology


def deploy(out):
    spec, docs = render()
    before = baseline(out)
    require(len(before[m1.CORE]) == 15 and len(before[m1.NS]) == 3, 'Expected unchanged single-UE starting set')
    require(not m1.kube('get', 'namespace', NS, '--ignore-not-found', '-o', 'name').stdout.strip(),
            'Fixture namespace already exists; inspect rather than overwrite')
    for kind in ['deployment', 'configmap']:
        require(not m1.kube('-n', m1.CORE, 'get', kind, 'cnf-a-ue2', '--ignore-not-found', '-o', 'name').stdout.strip(),
                'UE2 fixture name already exists')
    ports = {r['nodePort'] for r in spec['routes']}
    used = json.loads(m1.kube('get', 'services', '-A', '-o', 'json').stdout)['items']
    require(not ports.intersection(p.get('nodePort') for s in used for p in s['spec'].get('ports', [])),
            'Test NodePort already allocated')
    m1.save(out, 'subscriber-create.txt', m1.run(['bash', ROOT / 'scripts/create-test-subscriber.sh',
                                               ROOT / spec['ue2Subscriber']], timeout=60).stdout)
    ns_file = out / 'namespace.yaml'
    m1.save(out, ns_file.name, yaml.safe_dump(docs[0], sort_keys=False))
    m1.save(out, 'namespace-apply.txt', m1.kube('apply', '-f', ns_file).stdout)
    manifest = out / 'manifest.yaml'
    m1.save(out, 'server-dry-run.txt', m1.kube('apply', '--dry-run=server', '-f', manifest).stdout)
    m1.save(out, 'apply.txt', m1.kube('apply', '-f', manifest).stdout)
    for d in docs:
        if d['kind'] == 'Deployment':
            name, ns = d['metadata']['name'], d['metadata']['namespace']
            m1.save(out, name + '-rollout.txt', m1.kube('-n', ns, 'rollout', 'status', 'deployment/' + name,
                    '--timeout=120s', timeout=130).stdout)
    state, _ = verify_fixture(out / 'live-after')
    same_original(before, state)
    return {'originalPodsUnchanged': 18, 'addedPods': 6, 'state': VERSION}


def command(user, route, direction, spec, target):
    args = ['kubectl', '-n', m1.CORE, 'exec', user['pod'], '--', 'iperf3', '-c', target, '-B', user['ip'],
            '-p', str(route['nodePort']), '-P', '1', '-b', str(spec['offeredMbpsPerUE']) + 'M', '-t',
            str(spec['durationSeconds']), '-l', str(spec['blockBytes']), '-J', '--get-server-output', '--connect-timeout', '5000']
    return args + (['-R'] if direction == 'downlink' else [])


def active_window(data):
    intervals = [i['sum'] for i in data['intervals'] if i['sum']['bytes'] > 0]
    require(intervals, 'No positive traffic intervals')
    timestamp = data['start']['timestamp']['timesecs']
    # The same VM3 clock timestamps both clients. Quantization loses less than 1 second.
    return timestamp + intervals[0]['start'] + 1, timestamp + intervals[-1]['end']


def overlap_lower_bound(items):
    windows = [active_window(data) for data in items]
    return max(0, min(w[1] for w in windows) - max(w[0] for w in windows))


def validate_flow(data, row, route, spec, topology, logs, backend_logs):
    user = topology['users'][route['user']]
    require(data['start']['test_start']['duration'] == spec['durationSeconds'] and
            data['end']['sum_received']['seconds'] >= spec['durationSeconds'] * .95 and
            all(c['local_host'] == user['ip'] and c['remote_host'] == topology['target'] and
                c['remote_port'] == route['nodePort'] for c in data['start']['connected']) and
            bool(data['start']['connected']), 'Wrong bound UE/destination or incomplete measurement')
    entries = []
    for line in logs.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get('experiment') == VERSION and item.get('test_user') == route['user']:
            entries.append(json.dumps(item))
    name = route_name(route)
    mapped = {'name': route['businessClass'], 'listener': name, 'cluster': name, 'listenPort': route['listenPort']}
    result = m4.validate_flow(data, '\n'.join(entries), backend_logs[name],
        '\n'.join(v for k, v in backend_logs.items() if k != name), mapped,
        {'configVersion': VERSION, 'minimumPayloadBytes': 16384},
        topology['services'][name]['spec']['clusterIP'], topology['envoy']['ip'],
        row['startedAt'], row['trafficEndedAt'], row['direction'], spec['durationSeconds'])
    require(data['start']['test_start']['reverse'] == int(row['direction'] == 'downlink'), 'Wrong traffic direction')
    return result


def run_group(folder, routes, direction, number, spec, state, topology):
    folder.mkdir()
    m1.assert_steady(state, snapshot())
    upf, _ = m1.one_pod(state, m1.CORE, 'free5gc-helm-free5gc-upf-upf')
    first = m1.upf_observation(folder, 'before', upf)
    jobs, samples, rows, raw = [], [], [], []
    try:
        for route in routes:
            sub = folder / route['user']
            sub.mkdir()
            row = {'round': number, 'businessClass': route['businessClass'], 'direction': direction,
                   'user': route['user'], 'outcome': 'failed', 'startedAt': m1.now()}
            args = command(topology['users'][route['user']], route, direction, spec, topology['target'])
            m1.save(sub, 'command.json', args)
            jobs.append((route, sub, row, subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)))
        deadline = time.monotonic() + spec['durationSeconds'] + 30
        while any(p.poll() is None for _, _, _, p in jobs):
            current = snapshot()
            m1.save(folder, 'guard-pods.json', current)
            m1.assert_steady(state, current)
            memory = int(m1.kube('-n', m1.CORE, 'exec', upf, '--', 'cat', '/sys/fs/cgroup/memory.current', timeout=5).stdout)
            samples.append({'time': m1.now(), 'upfMemoryBytes': memory})
            m1.save(folder, 'resource-samples.json', samples)
            require(memory < spec['maximumUpfMemoryBytes'] and time.monotonic() < deadline, 'Memory guard or deadline exceeded')
            time.sleep(.5)
        for route, sub, row, process in jobs:
            stdout, stderr = process.communicate(timeout=5)
            row['trafficEndedAt'] = m1.now()
            m1.save(sub, 'iperf.json', stdout)
            m1.save(sub, 'iperf.stderr.txt', stderr)
            require(process.returncode == 0, 'UE iperf failed; retain failed batch')
            raw.append(json.loads(stdout))
        overlap = overlap_lower_bound(raw) if len(raw) == 2 else None
        require(overlap is None or overlap >= spec['minimumOverlapSeconds'], 'Traffic did not overlap for the required window')
        last = m1.upf_observation(folder, 'after', upf)
        require(all(b > a for a, b in zip(m1.path_counters(first['interfaces']), m1.path_counters(last['interfaces']))),
                'UPF bidirectional counters did not increase')
        started = min(row['startedAt'] for _, _, row, _ in jobs)
        deadline = time.monotonic() + 20
        while True:
            logs = m1.kube('-n', NS, 'logs', topology['envoy']['pod'], '--since-time=' + started).stdout
            m1.save(folder, 'envoy-access.txt', logs)
            backend_logs = {}
            for name, pod in topology['backends'].items():
                text = m1.kube('-n', NS, 'logs', pod['pod'], '--since-time=' + started).stdout
                m1.save(folder, name + '.txt', text)
                backend_logs[name] = text
            try:
                rows = []
                for (route, sub, row, _), data in zip(jobs, raw):
                    result = validate_flow(data, row, route, spec, topology, logs, backend_logs)
                    rows.append({**row, **result, 'outcome': 'passed'})
                break
            except m1.PendingProxyLogs:
                require(time.monotonic() < deadline, 'Proxy logs did not drain before deadline')
                m1.assert_steady(state, snapshot())
                time.sleep(.5)
        m1.assert_steady(state, snapshot())
        result = {'outcome': 'passed', 'rows': rows, 'overlapLowerBoundSeconds': overlap}
        m1.save(folder, 'summary.json', result)
        print(f'{folder.name}: {len(rows)} sessions passed; overlap={overlap}', flush=True)
        return result
    finally:
        for route, sub, row, process in jobs:
            if process.poll() is None:
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate(timeout=5)
                m1.save(sub, 'iperf.json', stdout)
                m1.save(sub, 'iperf.stderr.txt', stderr)
                m1.kube('-n', m1.CORE, 'exec', topology['users'][route['user']]['pod'], '--',
                        'pkill', '-TERM', '-x', 'iperf3', required=False)
        m1.save(folder, 'resource-samples.json', samples)


def measure(out, mode):
    spec, _ = config()
    state, topology = verify_fixture(out / 'live-before')
    m1.save(out, 'before-pods.json', state)
    groups, rows = [], []
    count = spec['roundsPerClassPerDirection'] if mode == 'concurrent' else 1
    for number in range(1, count + 1):
        for direction in ['uplink', 'downlink']:
            for name in ['core', 'edge']:
                routes = [r for r in spec['routes'] if r['businessClass'] == name]
                batches = [[r] for r in routes] if mode == 'individual' else [routes]
                for batch in batches:
                    suffix = '-' + batch[0]['user'] if mode == 'individual' else ''
                    folder = out / f'{number:02d}-{direction}-{name}{suffix}'
                    result = run_group(folder, batch, direction, number, spec, state, topology)
                    groups.append({'folder': folder.name, **result})
                    rows.extend(result['rows'])
                    m1.save(out, 'groups.json', groups)
    after, _ = verify_fixture(out / 'live-after')
    m1.assert_steady(state, after)
    m1.save(out, 'after-pods.json', after)
    cookies = [r['cookie'] for r in rows]
    require(len(set(cookies)) == len(cookies), 'Session cookie reused')
    return {'mode': mode, 'sessions': len(rows), 'groups': len(groups), 'uniqueCookies': len(set(cookies)),
            'proxyConnections': sum(r['proxyConnections'] for r in rows), 'originalPodsUnchanged': 18,
            'allMonitoredPodsUnchanged': 24, 'minimumOverlapLowerBoundSeconds': min(
                (g['overlapLowerBoundSeconds'] for g in groups if g['overlapLowerBoundSeconds'] is not None), default=None),
            'fullAcceptance': mode == 'concurrent' and len(rows) == 40}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['render', 'deploy', 'check', 'individual', 'smoke', 'concurrent'])
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    spec, docs = render()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    inputs = [SPEC, Path(__file__), ROOT / spec['ue2Subscriber'], ROOT / 'scripts/create-test-subscriber.sh',
              base.PROFILE, ROOT / 'scripts/cnf-profile.py', *[ROOT / p for p in base.profile()['inputSha256LF']]]
    m1.save(out, 'metadata.json', {'route': 'R2', 'stage': 'R2.S4', 'task': 'A', 'configVersion': VERSION,
        'command': args.command, 'startedAt': m1.now(), 'evidencePath': str(out),
        'sha256LF': {str(p.relative_to(ROOT)): m2.sha(p) for p in inputs}})
    m1.save(out, 'experiment.json', spec)
    m1.save(out, 'manifest.yaml', yaml.safe_dump_all(docs, sort_keys=False))
    result = {'outcome': 'failed', 'command': args.command}
    try:
        if args.command == 'deploy':
            result['evidence'] = deploy(out)
        elif args.command == 'check':
            state, topology = verify_fixture(out / 'live')
            result['evidence'] = {'users': topology['users'], 'monitoredPods': sum(map(len, state.values()))}
        elif args.command != 'render':
            result['evidence'] = measure(out, args.command)
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
        print('CNF multi-UE ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(1)
