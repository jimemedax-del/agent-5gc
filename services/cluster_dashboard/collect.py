"""Executed on the cluster access host. Only fixed read operations are allowed.

This file deliberately uses the Python standard library so it can be piped to
python3 over SSH without installing anything on the VMs. No secrets, ConfigMap
contents, environment variables or arbitrary log bodies leave the access host.
"""
import concurrent.futures
import datetime as dt
import http.client
import io
import json
import os
import re
import subprocess
import time


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def command(args, timeout=12, binary=False):
    env = os.environ.copy()
    env.setdefault('KUBECONFIG', os.path.expanduser('~/.kube/config'))
    result = subprocess.run(['kubectl', '--request-timeout=8s', *args],
                            capture_output=True, text=not binary, timeout=timeout, env=env)
    if result.returncode:
        error = result.stderr.decode('utf-8', errors='replace') if binary else result.stderr
        raise RuntimeError(error.strip()[-500:] or 'kubectl failed')
    return result.stdout


def get(args):
    return json.loads(command(args))


def resources():
    return get(['get', 'nodes,pods,services,endpointslices,networkpolicies,deployments,statefulsets,persistentvolumeclaims',
                '--all-namespaces', '-o', 'json'])['items']


def measure(pod, network=None):
    """eth0 counters are aggregate interface counters, never per-edge evidence."""
    meta, spec = pod['metadata'], pod['spec']
    name, namespace = meta['name'], meta['namespace']
    containers = spec.get('containers', [])
    base = ['-n', namespace, 'exec', name, '-c', containers[0]['name'], '--']
    item = {'id': namespace + '/' + name, 'uid': meta['uid'], 'at': now()}
    if network and network.get('uid') == meta['uid']:
        item.update(network)
    else:
        try:
            raw = command(base + ['cat', '/proc/net/dev'], timeout=10)
            for line in raw.splitlines():
                if line.strip().startswith('eth0:'):
                    fields = line.split(':', 1)[1].split()
                    item['rxBytes'], item['txBytes'] = int(fields[0]), int(fields[8])
                    item['counterSource'] = 'container /proc/net/dev fallback'
                    break
            if 'rxBytes' not in item:
                item['error'] = 'eth0 接口不可用'
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
            item['error'] = str(exc)[:250]
    # Observing a tunnel's existence does not prove registration or reachability.
    if name.startswith(('ueransim-ue-', 'cnf-a-ue2-')):
        try:
            item['interfaces'] = [
                {'name': x.get('ifname'), 'index': x.get('ifindex'), 'state': x.get('operstate'),
                 'addresses': [a['local'] for a in x.get('addr_info', []) if a.get('family') == 'inet']}
                for x in json.loads(command(base + ['ip', '-j', 'address', 'show']))
                if x.get('ifname', '').startswith('uesimtun')]
        except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
            item['tunnelError'] = str(exc)[:250]
    if name.startswith(('ueransim-ue-', 'cnf-a-ue2-')) or meta.get('labels', {}).get('nf') == 'upf':
        try:
            links = json.loads(command(base + ['ip', '-s', '-j', 'link', 'show']))
            at = now()
            item['interfaceStats'] = []
            for link in links:
                interface = link.get('ifname', '')
                if not interface.startswith('uesimtun') and interface != 'upfgtp':
                    continue
                addresses = next((i['addresses'] for i in item.get('interfaces', [])
                                  if i['name'] == interface and i.get('index') == link.get('ifindex')), [])
                stats = link.get('stats64') or link.get('stats') or {}
                if not all('bytes' in stats.get(d, {}) for d in ('rx', 'tx')):
                    continue
                identity = ':'.join([meta['uid'], str(link['ifindex']), interface, ','.join(addresses)])
                item['interfaceStats'].append({'uid': identity, 'podUid': meta['uid'], 'name': interface,
                    'index': link['ifindex'], 'addresses': addresses, 'at': at,
                    'rxBytes': stats['rx']['bytes'], 'txBytes': stats['tx']['bytes'],
                    'rxPackets': stats['rx'].get('packets'), 'txPackets': stats['tx'].get('packets'),
                    'source': 'ip -s -j link · Linux L3 interface counters'})
        except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError, KeyError) as exc:
            item['interfaceStatsError'] = str(exc)[:250]
    if 'envoy' in name:
        try:
            # Envoy's existing image has bash/timeout but no curl. Use its local
            # admin socket through a fixed HTTP GET, without a sidecar/install.
            raw = command(base + ['timeout', '4', 'bash', '-c',
                "exec 3<>/dev/tcp/127.0.0.1/9901; printf 'GET /stats?format=json HTTP/1.1\\r\\nHost: localhost\\r\\nConnection: close\\r\\n\\r\\n' >&3; cat <&3"], binary=True)
            class Socket:
                def makefile(self, *args):
                    return io.BytesIO(raw)
            response = http.client.HTTPResponse(Socket())
            response.begin()
            if response.status != 200:
                raise RuntimeError('Envoy admin HTTP ' + str(response.status))
            raw = response.read()
            item['envoyAt'] = now()
            item['envoy'] = {x['name']: x['value'] for x in json.loads(raw).get('stats', [])
                             if re.match(r'^(cluster\.[^.]+\.(upstream_cx_(active|total)|upstream_cx_(rx|tx)_bytes_total)|tcp\.[^.]+\.(downstream_cx_(total|active)|downstream_cx_(rx|tx)_bytes_total))$', x.get('name', ''))
                             and isinstance(x.get('value'), (int, float))}
        except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
            item['envoyError'] = str(exc)[:250]
    return item


def access_bindings(pods, yaml):
    """Only export gNB discovery targets, never subscription/authentication fields."""
    bindings = []
    cache = {}
    for pod in pods:
        meta = pod['metadata']
        if not meta['name'].startswith(('ueransim-ue-', 'cnf-a-ue2-')):
            continue
        targets = []
        for volume in pod.get('spec', {}).get('volumes', []):
            name = volume.get('configMap', {}).get('name')
            if not name:
                continue
            key = (meta['namespace'], name)
            if key not in cache:
                cache[key] = get(['-n', meta['namespace'], 'get', 'configmap', name, '-o', 'json'])
            for value in cache[key].get('data', {}).values():
                config = yaml.safe_load(value)
                if isinstance(config, dict) and isinstance(config.get('gnbSearchList'), list):
                    targets.extend(x for x in config['gnbSearchList'] if isinstance(x, str))
        bindings.append({'podUid': meta['uid'], 'namespace': meta['namespace'],
                         'targets': sorted(set(targets))})
    return bindings


def proxy_config():
    """Extract only non-sensitive TCP listener -> cluster -> DNS destinations."""
    output = []
    try:
        import yaml
    except ImportError:
        return {'routes': [], 'error': '远端缺少 PyYAML；未解析 Envoy 配置关系'}
    pods = get(['get', 'pods', '-A', '-o', 'json'])['items']
    mounted = {(p['metadata']['namespace'], v['configMap']['name'])
               for p in pods if 'envoy' in p['metadata']['name']
               for v in p.get('spec', {}).get('volumes', []) if 'configMap' in v}
    for ns in ('cnf-lab', 'cnf-multi-ue'):
        try:
            cms = get(['-n', ns, 'get', 'configmaps', '-o', 'json'])['items']
            for cm in cms:
                raw = cm.get('data', {}).get('envoy.yaml')
                if not raw or (ns, cm['metadata']['name']) not in mounted:
                    continue
                static = (yaml.safe_load(raw) or {}).get('static_resources', {})
                clusters = {}
                for c in static.get('clusters', []):
                    targets = []
                    for ep in c.get('load_assignment', {}).get('endpoints', []):
                        for lb in ep.get('lb_endpoints', []):
                            addr = lb.get('endpoint', {}).get('address', {}).get('socket_address', {})
                            targets.append({'host': addr.get('address'), 'port': addr.get('port_value')})
                    clusters[c['name']] = targets
                for listener in static.get('listeners', []):
                    for chain in listener.get('filter_chains', []):
                        for f in chain.get('filters', []):
                            config = f.get('typed_config', {})
                            if f.get('name') == 'envoy.filters.network.tcp_proxy':
                                output.append({'namespace': ns, 'configMap': cm['metadata']['name'],
                                               'listener': listener['name'],
                                               'port': listener.get('address', {}).get('socket_address', {}).get('port_value'),
                                               'statPrefix': config.get('stat_prefix'), 'cluster': config.get('cluster'),
                                               'targets': clusters.get(config.get('cluster'), [])})
        except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
            return {'routes': output, 'error': str(exc)[:300]}
    result = {'routes': output}
    try:
        result['accessBindings'] = access_bindings(pods, yaml)
    except Exception as exc:
        # YAML diagnostics can embed source lines containing subscription keys.
        result['accessBindingsError'] = 'gNB 发现目标解析失败：' + type(exc).__name__
    return result


def sanitize(items):
    clean = []
    for item in items:
        m = item.get('metadata', {})
        # Do not transmit arbitrary annotations, managedFields, env or mounts.
        base = {'kind': item['kind'], 'metadata': {k: m[k] for k in
                ('name', 'namespace', 'uid', 'creationTimestamp', 'labels', 'ownerReferences') if k in m}}
        s, status, kind = item.get('spec', {}), item.get('status', {}), item['kind']
        if kind == 'Node':
            base['spec'] = {k: s.get(k) for k in ('unschedulable', 'podCIDR', 'taints')}
            base['status'] = {k: status.get(k) for k in ('conditions', 'addresses', 'capacity', 'allocatable', 'nodeInfo')}
        elif kind == 'Pod':
            base['spec'] = {'nodeName': s.get('nodeName'), 'hostNetwork': s.get('hostNetwork', False),
                            'configMaps': [v['configMap']['name'] for v in s.get('volumes', []) if 'configMap' in v],
                            'containers': [{k: c[k] for k in ('name', 'image', 'resources', 'ports') if k in c}
                                           for c in s.get('containers', [])]}
            base['status'] = {k: status.get(k) for k in ('phase', 'podIP', 'hostIP', 'startTime', 'conditions', 'reason', 'message')}
            base['status']['containerStatuses'] = [{k: c[k] for k in ('name', 'ready', 'restartCount', 'state', 'lastState') if k in c}
                                                   for c in status.get('containerStatuses', [])]
            base['status']['initContainerStatuses'] = [{k: c[k] for k in ('name', 'ready', 'restartCount', 'state') if k in c}
                                                       for c in status.get('initContainerStatuses', [])]
        elif kind == 'Service':
            base['spec'] = {k: s.get(k) for k in ('type', 'clusterIP', 'ports', 'selector', 'externalTrafficPolicy')}
        elif kind == 'EndpointSlice':
            base.update({'addressType': item.get('addressType'), 'ports': item.get('ports', []),
                         'endpoints': [{k: e[k] for k in ('addresses', 'conditions', 'targetRef', 'nodeName') if k in e}
                                       for e in item.get('endpoints', [])]})
        elif kind == 'NetworkPolicy':
            base['spec'] = s
        elif kind in ('Deployment', 'StatefulSet'):
            base['spec'] = {'replicas': s.get('replicas', 1)}
            base['status'] = {k: status.get(k) for k in ('replicas', 'readyReplicas', 'availableReplicas', 'conditions')}
        elif kind == 'PersistentVolumeClaim':
            base['spec'] = {k: s.get(k) for k in ('storageClassName', 'accessModes', 'resources', 'volumeName')}
            base['status'] = {'phase': status.get('phase'), 'capacity': status.get('capacity')}
        clean.append(base)
    return clean


def collect():
    started = time.monotonic()
    output = {'schemaVersion': 1, 'startedAt': now(), 'errors': {}}
    tasks = {
        'resources': resources,
        'nodeMetrics': lambda: get(['get', '--raw', '/apis/metrics.k8s.io/v1beta1/nodes']).get('items', []),
        'podMetrics': lambda: get(['get', '--raw', '/apis/metrics.k8s.io/v1beta1/pods']).get('items', []),
        'apiReady': lambda: command(['get', '--raw', '/readyz']),
        'events': lambda: get(['get', 'events', '-A', '-o', 'json']).get('items', []),
        'proxyConfig': proxy_config,
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fn): key for key, fn in tasks.items()}
        for f in concurrent.futures.as_completed(futures):
            key = futures[f]
            try:
                output[key] = f.result()
            except Exception as exc:
                output['errors'][key] = str(exc)[:500]
    raw = output.get('resources', [])
    output['resources'] = sanitize(raw)
    # Events may contain infrastructure messages, but never return full objects.
    output['events'] = [{
        'namespace': e['metadata'].get('namespace'), 'name': e['metadata']['name'],
        'at': e.get('lastTimestamp') or e.get('eventTime') or e['metadata'].get('creationTimestamp'),
        'type': e.get('type'), 'reason': e.get('reason'), 'message': e.get('message', '')[:600],
        'object': e.get('involvedObject', {}).get('name'), 'count': e.get('count', 1)}
        for e in output.get('events', [])]
    output['events'] = sorted(output['events'], key=lambda x: x.get('at') or '', reverse=True)[:40]
    pods = [p for p in raw if p['kind'] == 'Pod' and p.get('status', {}).get('phase') == 'Running']
    # Kubelet supplies network counters even for distroless Pods without cat.
    networks = {}
    def node_network(node):
        summary = get(['get', '--raw', '/api/v1/nodes/' + node['metadata']['name'] + '/proxy/stats/summary'])
        found = {}
        for p in summary.get('pods', []):
            ref, network = p.get('podRef', {}), p.get('network', {})
            eth = next((n for n in network.get('interfaces', []) if n.get('name') == 'eth0'), None)
            if eth and 'rxBytes' in eth and 'txBytes' in eth:
                found[ref['namespace'] + '/' + ref['name']] = {
                    'uid': ref['uid'], 'at': network['time'], 'rxBytes': eth['rxBytes'],
                    'txBytes': eth['txBytes'], 'counterSource': 'kubelet stats/summary · eth0'}
        return found
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        queries = {pool.submit(node_network, n): n['metadata']['name'] for n in raw if n['kind'] == 'Node'}
        for f in concurrent.futures.as_completed(queries):
            try:
                networks.update(f.result())
            except Exception as exc:
                output['errors']['network/' + queries[f]] = str(exc)[:300]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        output['telemetry'] = list(pool.map(lambda p: measure(p, networks.get(p['metadata']['namespace'] + '/' + p['metadata']['name'])), pods))
    output['observedAt'] = now()
    output['durationSeconds'] = round(time.monotonic() - started, 3)
    return output


if __name__ == '__main__':
    print(json.dumps(collect(), ensure_ascii=False, separators=(',', ':')))
