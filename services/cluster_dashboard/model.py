"""Build a conservative display model from timestamped Kubernetes observations."""
import datetime as dt
import re


DESCRIPTIONS = {
    'AMF': '接入与移动性管理：接收 gNB 信令，处理 UE 注册和连接。',
    'SMF': '会话管理：建立 PDU 会话，通过 N4/PFCP 配置 UPF。',
    'UPF': '用户面转发：处理 GTP-U 隧道，将 UE 数据转向业务网络。',
    'AUSF': '认证服务：参与 UE 身份鉴权。',
    'UDM': '统一数据管理：管理用户身份与订阅逻辑。',
    'UDR': '统一数据存储：提供订阅及策略数据查询。',
    'NRF': '网络功能注册与发现：记录核心网功能的服务位置。',
    'PCF': '策略控制：提供策略决策；当前原生 5G QoS 未验收。',
    'NSSF': '网络切片选择：提供切片选择相关服务。',
    'NEF': '网络能力开放：提供受控的外部能力接口。',
    'CHF': '计费功能：处理计费相关服务。',
    'MongoDB': '核心网数据库：保存用户订阅与平台数据。',
    'WebUI': 'free5GC 管理入口。',
    'gNB': 'UERANSIM 模拟基站：连接 UE 与核心网；不包含真实无线调度。',
    'UE1': '第一个模拟终端：通过 gNB 注册和建立 PDU 会话。',
    'UE2': '第二个模拟终端：使用独立身份参与双用户实验。',
    'Envoy': 'TCP 代理：按既有监听端口将连接转发至 core/edge 后端。',
    'Core': '中心业务后端：提供受控 iperf3 实验服务。',
    'Edge': '边缘业务后端：部署在边缘节点；位置不自动证明低时延。',
    'C1 下行入口': '双用户下行共享瓶颈实验端点，容器 tc 配置需另行验收。',
    'C2 上行入口': '双用户上行共享瓶颈实验端点，容器 tc 配置需另行验收。',
    'CoreDNS': '集群 DNS：解析 Kubernetes Service 名称。',
    'Metrics': '采集节点和 Pod 的 CPU、内存用量。',
    'Storage': '本地存储卷供应服务。',
}


def stamp(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def fresh(at, observed_at, maximum_age=120):
    try:
        return bool(at) and 0 <= stamp(observed_at) - stamp(at) <= maximum_age
    except (ValueError, TypeError, AttributeError):
        return False


def quantity(value, memory=False):
    if value is None:
        return None
    match = re.fullmatch(r'([0-9.]+)([A-Za-z]*)', str(value))
    if not match:
        return None
    number, unit = float(match[1]), match[2]
    factors = {'n': 1e-9, 'u': 1e-6, 'm': 1e-3, '': 1,
               'Ki': 1024, 'Mi': 1024**2, 'Gi': 1024**3, 'Ti': 1024**4,
               'K': 1e3, 'M': 1e6, 'G': 1e9, 'T': 1e12}
    return number * factors[unit] if unit in factors else None


def component(name):
    for key, text in [('ueransim-ue', 'UE1'), ('cnf-a-ue2', 'UE2'), ('ueransim-gnb', 'gNB'),
                      ('envoy', 'Envoy'), ('cnf-c2-endpoint', 'C2 上行入口'),
                      ('cnf-c-endpoint', 'C1 下行入口'), ('backend', 'Core'),
                      ('cnf-a-core', 'Core'), ('edge', 'Edge'), ('mongodb', 'MongoDB'),
                      ('coredns', 'CoreDNS'), ('metrics-server', 'Metrics'),
                      ('local-path', 'Storage'), ('webui', 'WebUI')]:
        if key in name:
            return text
    m = re.search(r'free5gc-(amf|smf|upf|ausf|udm|udr|nrf|pcf|nssf|nef|chf)-', name)
    return m[1].upper() if m else name.split('-')[0]


def node_role(labels):
    if labels.get('5gc.free5gc.org/plane') == 'control':
        return 'VM1', '控制面与中心服务', '承载 k3s Server、核心网控制功能、数据库、代理和中心后端。'
    if labels.get('5gc.free5gc.org/plane') == 'user':
        return 'VM2', '用户面转发', '承载 UPF 与 gtp5g 用户面隧道；负责 UE 数据转发。'
    if labels.get('5gc.free5gc.org/role') == 'ran-test':
        return 'VM3', '模拟接入与边缘服务', '承载 UERANSIM gNB、两个 UE 和边缘业务后端。'
    return 'Node', '集群节点', '角色由当前 Kubernetes 标签决定。'


def rate(current, previous, key):
    if not previous or current.get('uid') != previous.get('uid'):
        return None
    seconds = stamp(current['at']) - stamp(previous['at'])
    if seconds <= 0 or seconds > 90:
        return None
    a, b = current.get(key), previous.get(key)
    if a is None or b is None or a < b:
        return None
    return round((a - b) * 8 / seconds / 1e6, 6)


def build(raw, previous=None):
    previous = previous or {}
    items = raw.get('resources', [])
    groups = {}
    for item in items:
        groups.setdefault(item['kind'], []).append(item)
    old_telemetry = {t['id']: t for t in previous.get('telemetry', [])}
    telemetry = {t['id']: t for t in raw.get('telemetry', [])}
    node_metrics = {x['metadata']['name']: x for x in raw.get('nodeMetrics', [])}
    pod_metrics = {x['metadata']['namespace'] + '/' + x['metadata']['name']: x for x in raw.get('podMetrics', [])}
    nodes, pods, services, edges = [], [], [], []
    for node in groups.get('Node', []):
        meta, status = node['metadata'], node['status']
        labels = meta.get('labels', {})
        alias, role, desc = node_role(labels)
        usage = node_metrics.get(meta['name'], {}).get('usage', {})
        conditions = status.get('conditions') or []
        ready = any(c['type'] == 'Ready' and c['status'] == 'True' for c in conditions)
        pressure = [c['type'] for c in conditions if c['type'].endswith('Pressure') and c['status'] == 'True']
        nodes.append({'id': 'node/' + meta['name'], 'name': meta['name'], 'uid': meta['uid'],
                      'alias': alias, 'role': role, 'description': desc, 'labels': labels,
                      'ready': ready, 'pressure': pressure, 'schedulable': not node['spec'].get('unschedulable'),
                      'ip': next((a['address'] for a in status.get('addresses', []) if a['type'] == 'InternalIP'), '—'),
                      'cpuCores': quantity(status.get('allocatable', {}).get('cpu')),
                      'memoryBytes': quantity(status.get('allocatable', {}).get('memory'), True),
                      'cpuUsed': quantity(usage.get('cpu')), 'memoryUsed': quantity(usage.get('memory'), True),
                      'metricsAt': node_metrics.get(meta['name'], {}).get('timestamp'),
                      'info': status.get('nodeInfo'), 'conditions': conditions,
                      'podCIDR': node['spec'].get('podCIDR'), 'createdAt': meta.get('creationTimestamp')})
    nodes.sort(key=lambda n: n['alias'])
    for pod in groups.get('Pod', []):
        meta, spec, status = pod['metadata'], pod['spec'], pod['status']
        key = meta['namespace'] + '/' + meta['name']
        containers = status.get('containerStatuses') or []
        inits = status.get('initContainerStatuses') or []
        ready = any(c['type'] == 'Ready' and c['status'] == 'True' for c in status.get('conditions') or [])
        comp = component(meta['name'])
        tel = telemetry.get(key, {})
        old_interfaces = {i['uid']: i for i in old_telemetry.get(key, {}).get('interfaceStats', [])}
        interface_traffic = []
        for interface in tel.get('interfaceStats', []):
            if interface.get('podUid') != meta['uid']:
                continue
            previous_interface = old_interfaces.get(interface['uid'])
            valid = fresh(interface.get('at'), raw['observedAt'])
            interface_traffic.append(dict(interface,
                rxMbps=rate(interface, previous_interface, 'rxBytes') if valid else None,
                txMbps=rate(interface, previous_interface, 'txBytes') if valid else None,
                windowStart=previous_interface.get('at') if previous_interface else None))
        metrics = pod_metrics.get(key, {}).get('containers', [])
        rows = [{'name': c['name'], 'image': c.get('image'), 'resources': c.get('resources', {}),
                 'ports': c.get('ports', []), 'status': next((x for x in containers if x['name'] == c['name']), {})}
                for c in spec.get('containers', [])]
        item = {'id': 'pod/' + key, 'key': key, 'name': meta['name'], 'namespace': meta['namespace'],
                'uid': meta['uid'], 'node': spec.get('nodeName'), 'ip': status.get('podIP'),
                'phase': status.get('phase'), 'ready': ready, 'component': comp,
                'description': DESCRIPTIONS.get(comp, '集群工作负载，详见容器和资源信息。'),
                'restarts': sum(c.get('restartCount', 0) for c in containers + inits),
                'readyContainers': sum(bool(c.get('ready')) for c in containers), 'containerCount': len(rows),
                'cpuUsed': sum(quantity(c['usage']['cpu']) or 0 for c in metrics) if metrics else None,
                'memoryUsed': sum(quantity(c['usage']['memory'], True) or 0 for c in metrics) if metrics else None,
                'metricsAt': pod_metrics.get(key, {}).get('timestamp'),
                'rxMbps': rate(tel, old_telemetry.get(key), 'rxBytes') if tel else None,
                'txMbps': rate(tel, old_telemetry.get(key), 'txBytes') if tel else None,
                'telemetry': tel, 'interfaceTraffic': interface_traffic,
                'labels': meta.get('labels', {}), 'containers': rows,
                'createdAt': meta.get('creationTimestamp'), 'conditions': status.get('conditions') or [],
                'hostNetwork': spec.get('hostNetwork', False), 'configMaps': spec.get('configMaps', [])}
        pods.append(item)
    pods.sort(key=lambda p: (p['namespace'], p['name']))
    pod_ids = {p['id']: p for p in pods}
    for svc in groups.get('Service', []):
        m, spec = svc['metadata'], svc['spec']
        sid = 'svc/' + m['namespace'] + '/' + m['name']
        endpoints = []
        for sl in groups.get('EndpointSlice', []):
            sm = sl['metadata']
            if sm.get('namespace') != m['namespace'] or sm.get('labels', {}).get('kubernetes.io/service-name') != m['name']:
                continue
            for ep in sl.get('endpoints', []):
                ref = ep.get('targetRef', {})
                pid = 'pod/' + ref.get('namespace', m['namespace']) + '/' + ref.get('name', '')
                target = pid if pid in pod_ids and (not ref.get('uid') or ref['uid'] == pod_ids[pid]['uid']) else None
                endpoint = {'addresses': ep.get('addresses', []), 'ready': ep.get('conditions', {}).get('ready'),
                            'target': target, 'node': ep.get('nodeName')}
                endpoints.append(endpoint)
                if target:
                    edges.append({'id': sid + '>' + target, 'source': sid, 'target': target,
                                  'kind': 'endpoint', 'label': 'EndpointSlice', 'ready': endpoint['ready'],
                                  'evidence': '当前 Kubernetes EndpointSlice；表示服务端点绑定，不证明有实际连接。'})
        services.append({'id': sid, 'name': m['name'], 'namespace': m['namespace'],
                         'ip': spec.get('clusterIP'), 'type': spec.get('type'), 'ports': spec.get('ports') or [],
                         'selector': spec.get('selector') or {}, 'endpoints': endpoints})
    service_ids = {s['id'] for s in services}
    routes = []
    for r in raw.get('proxyConfig', {}).get('routes', []):
        gateway = next((p for p in pods if p['component'] == 'Envoy' and p['namespace'] == r['namespace']
                        and r['configMap'] in p['configMaps']), None)
        if not gateway:
            continue
        tel = gateway['telemetry']
        old = old_telemetry.get(gateway['key'], {})
        stats = tel.get('envoy', {})
        prefix = 'cluster.' + r['cluster'] + '.'
        def stat_rate(field, stat_prefix=prefix):
            return rate({'at': tel.get('envoyAt', tel.get('at')), 'uid': tel.get('uid'), 'value': stats.get(stat_prefix + field)},
                        {'at': old.get('envoyAt', old.get('at')), 'uid': old.get('uid'), 'value': old.get('envoy', {}).get(stat_prefix + field)}
                        if old.get('envoyAt', old.get('at')) else None, 'value')
        rx, tx = stat_rate('upstream_cx_rx_bytes_total'), stat_rate('upstream_cx_tx_bytes_total')
        active = stats.get(prefix + 'upstream_cx_active')
        for target in r['targets']:
            bits = (target.get('host') or '').strip('.').split('.')
            target_ns = bits[1] if len(bits) >= 2 else r['namespace']
            sid = 'svc/' + target_ns + '/' + bits[0]
            route = dict(r, id=gateway['uid'] + ':' + r['listener'] + ':' + sid,
                         source=gateway['id'], target=sid, resolved=sid in service_ids,
                         rxMbps=rx, txMbps=tx, activeConnections=active,
                         downstreamRxMbps=stat_rate('downstream_cx_rx_bytes_total', 'tcp.' + r.get('statPrefix', '') + '.'),
                         downstreamTxMbps=stat_rate('downstream_cx_tx_bytes_total', 'tcp.' + r.get('statPrefix', '') + '.'),
                         statsAt=tel.get('envoyAt'), statsWindowStart=old.get('envoyAt'),
                         totalConnections=stats.get(prefix + 'upstream_cx_total'), statsAvailable=bool(stats))
            routes.append(route)
            edges.append({'id': gateway['id'] + '>' + sid + ':' + str(r['port']),
                          'source': gateway['id'], 'target': sid, 'kind': 'proxy',
                          'label': str(r['port']) + ' → ' + str(target.get('port')), 'route': route,
                          'evidence': '当前 Pod 挂载的 Envoy ConfigMap；速率为该 upstream cluster 的采样均值。'})
    workloads = [{'kind': w['kind'], 'name': w['metadata']['name'], 'namespace': w['metadata']['namespace'],
                  'desired': w['spec'].get('replicas', 1), 'ready': w['status'].get('readyReplicas') or 0}
                 for kind in ('Deployment', 'StatefulSet') for w in groups.get(kind, [])]
    pvcs = [{'name': p['metadata']['name'], 'namespace': p['metadata']['namespace'],
             'phase': p['status'].get('phase'), 'spec': p['spec'], 'capacity': p['status'].get('capacity')}
            for p in groups.get('PersistentVolumeClaim', [])]
    policies = [{'name': p['metadata']['name'], 'namespace': p['metadata']['namespace'], 'spec': p['spec']}
                for p in groups.get('NetworkPolicy', [])]
    result = {'observedAt': raw['observedAt'], 'startedAt': raw['startedAt'],
              'durationSeconds': raw['durationSeconds'], 'nodes': nodes, 'pods': pods,
              'services': services, 'edges': edges, 'routes': routes, 'workloads': workloads,
              'pvcs': pvcs, 'policies': policies, 'events': raw.get('events', []),
              'errors': dict(raw.get('errors', {})), 'apiReady': raw.get('apiReady', '').strip() == 'ok'}
    if raw.get('proxyConfig', {}).get('error'):
        result['errors']['proxyConfig'] = raw['proxyConfig']['error']
    if raw.get('proxyConfig', {}).get('accessBindingsError'):
        result['errors']['accessBindings'] = raw['proxyConfig']['accessBindingsError']
    result['trafficLinks'] = traffic_links(result, raw.get('proxyConfig', {}).get('accessBindings', []))
    result['checks'] = checks(result)
    return result


def interface_observation(pod):
    interfaces = [i for i in pod.get('interfaceTraffic', []) if
                  (pod['component'] in ('UE1', 'UE2') and i['name'].startswith('uesimtun')) or
                  (pod['component'] == 'UPF' and i['name'] == 'upfgtp')]
    if len(interfaces) != 1:
        return None
    i = interfaces[0]
    upf = pod['component'] == 'UPF'
    if not upf and not i.get('addresses'):
        return None
    return {'pod': pod['id'], 'podUid': pod['uid'], 'interface': i['name'],
            'label': pod['component'] + ' / ' + i['name'], 'at': i['at'],
            'windowStart': i.get('windowStart'),
            'uplinkMbps': i['rxMbps'] if upf else i['txMbps'],
            'downlinkMbps': i['txMbps'] if upf else i['rxMbps'],
            'scope': 'UPF 全用户 IP 接口总量' if upf else '该 UE 隧道内的全部 IP 业务'}


def compatible_windows(observations, at):
    try:
        if not observations or any(not o or not fresh(o.get('at'), at) or
                o.get('uplinkMbps') is None or o.get('downlinkMbps') is None for o in observations):
            return False
        starts = [stamp(o['windowStart']) for o in observations]
        ends = [stamp(o['at']) for o in observations]
        durations = [b - a for a, b in zip(starts, ends)]
        return min(durations) > 0 and min(ends) - max(starts) >= max(durations) * .5
    except (ValueError, TypeError, AttributeError, KeyError):
        return False


def traffic_links(data, bindings):
    """Activity correlations are explicitly NOT measured per-hop flow rates.

    No upstream edge is activated solely from backend throughput. UE tunnel
    counters, the UPF L3 interface and Envoy downstream counters remain separate
    observations, with aligned windows and lifecycle identities.
    """
    pods, services = data['pods'], data['services']
    ues = [p for p in pods if p['component'] in ('UE1', 'UE2')]
    gnbs = [p for p in pods if p['component'] == 'gNB']
    upfs = [p for p in pods if p['component'] == 'UPF']
    links = []
    def add(source, target, segment, scope, observations, forward, reverse):
        known = compatible_windows(observations, data['observedAt'])
        f, r = bool(known and forward), bool(known and reverse)
        any_activity = known and any(o['uplinkMbps'] > 0 or o['downlinkMbps'] > 0 for o in observations)
        state = 'active' if f or r else 'unmatched' if any_activity else 'idle' if known else 'unknown'
        links.append({'id': segment + ':' + source['uid'] + '>' + target['uid'],
                      'source': source['id'], 'target': target['id'], 'segment': segment,
                      'kind': 'endpoint-correlation', 'status': state,
                      'forwardActive': f, 'reverseActive': r, 'scope': scope,
                      'observations': [o for o in observations if o],
                      'evidence': '端点计数的活动关联，不是该连线逐包观测；不生成链路 Mbps，不能据此归因到某一业务或用户。'})
    bound_ues = []
    for ue in ues:
        binding = next((b for b in bindings if b.get('podUid') == ue['uid']), {})
        candidates = set()
        for host in binding.get('targets', []):
            for gnb in gnbs:
                if host == gnb.get('ip'):
                    candidates.add(gnb['id'])
            bits = host.strip('.').split('.')
            ns = bits[1] if len(bits) > 1 else ue['namespace']
            service = next((s for s in services if s['name'] == bits[0] and s['namespace'] == ns), None)
            if service:
                candidates.update(e['target'] for e in service['endpoints'] if e['ready'] is True and
                                  any(g['id'] == e['target'] for g in gnbs))
        if len(candidates) != 1:
            continue
        gnb = next(g for g in gnbs if g['id'] in candidates)
        observation = interface_observation(ue)
        add(ue, gnb, 'access', 'UE 挂载的 gNB 发现目标与当前 Ready 端点绑定；活动取自 UE 隧道，未直接观测模拟接入报文。',
            [observation], observation and observation['uplinkMbps'] is not None and observation['uplinkMbps'] > 0,
            observation and observation['downlinkMbps'] is not None and observation['downlinkMbps'] > 0)
        bound_ues.append(ue)
    if len(gnbs) == 1 and len(upfs) == 1 and bound_ues and len(bound_ues) == len(ues):
        upf, gnb = upfs[0], gnbs[0]
        upf_o = interface_observation(upf)
        ue_o = [interface_observation(p) for p in bound_ues]
        ul = upf_o and (upf_o['uplinkMbps'] or 0) > 0 and any(o and (o['uplinkMbps'] or 0) > 0 for o in ue_o)
        dl = upf_o and (upf_o['downlinkMbps'] or 0) > 0 and any(o and (o['downlinkMbps'] or 0) > 0 for o in ue_o)
        add(gnb, upf, 'n3', '当前单 gNB/单 UPF 架构下的端点活动关联，覆盖全部 UE/全部业务；未抓取 GTP-U，未按 TEID 匹配。',
            ue_o + [upf_o], ul, dl)
        for gateway in [p for p in pods if p['component'] == 'Envoy']:
            routes = [r for r in data['routes'] if r['source'] == gateway['id']]
            if not routes:
                continue
            rx, tx = [r['downstreamRxMbps'] for r in routes], [r['downstreamTxMbps'] for r in routes]
            ingress = {'pod': gateway['id'], 'podUid': gateway['uid'], 'label': gateway['namespace'] + ' / 代理入口',
                       'at': routes[0]['statsAt'], 'windowStart': routes[0]['statsWindowStart'],
                       'uplinkMbps': sum(rx) if all(v is not None for v in rx) else None,
                       'downlinkMbps': sum(tx) if all(v is not None for v in tx) else None,
                       'scope': '该代理所有已解析监听端口的 downstream 统计，不是某个 UE 的吞吐'}
            ul = upf_o and (upf_o['uplinkMbps'] or 0) > 0 and (ingress['uplinkMbps'] or 0) > 0
            dl = upf_o and (upf_o['downlinkMbps'] or 0) > 0 and (ingress['downlinkMbps'] or 0) > 0
            add(upf, gateway, 'n6', 'UPF IP 接口与该代理入口的同向活动关联；未按五元组匹配，无法证明两端属于同一条连接。',
                [upf_o, ingress], ul, dl)
    return links


def checks(data):
    rows = []
    def add(key, title, ok, detail, unknown=False):
        rows.append({'key': key, 'title': title, 'status': 'unknown' if unknown else 'pass' if ok else 'fail',
                     'detail': detail, 'at': data['observedAt']})
    nodes, pods = data['nodes'], data['pods']
    add('api', 'Kubernetes API', data['apiReady'], '/readyz 返回 ok' if data['apiReady'] else 'API 就绪检查未通过')
    bad = [n['name'] for n in nodes if not n['ready'] or n['pressure']]
    add('nodes', '节点就绪与压力', not bad and bool(nodes),
        '、'.join(bad) if bad else f'{len(nodes)} 个节点 Ready，无 Memory/Disk/PID 压力')
    active = [p for p in pods if p['phase'] not in ('Succeeded',)]
    bad = [p['name'] for p in active if not p['ready']]
    add('pods', 'Pod 就绪状态', not bad and bool(active),
        '、'.join(bad) if bad else f'{len(active)} 个非完成 Pod 全部 Ready；不代表业务链路已通过')
    bad = [w['name'] for w in data['workloads'] if w['ready'] < w['desired']]
    add('workloads', '工作负载副本', not bad, '、'.join(bad) if bad else f'{len(data["workloads"])} 个工作负载达到期望副本数')
    # Only selector-backed Services require matching Pod endpoints here.
    svcs = [s for s in data['services'] if s['selector']]
    bad = [s['namespace'] + '/' + s['name'] for s in svcs if not any(e['ready'] is True for e in s['endpoints'])]
    add('endpoints', 'Service 可用端点', not bad, '、'.join(bad) if bad else f'{len(svcs)} 个带 selector 的 Service 均有 Ready 端点')
    bad = [p['name'] for p in data['pvcs'] if p['phase'] != 'Bound']
    add('storage', '持久卷绑定', not bad, '、'.join(bad) if bad else f'{len(data["pvcs"])} 个 PVC 已绑定')
    ues = [p for p in pods if p['component'] in ('UE1', 'UE2')]
    errors = [p['name'] for p in ues if p['telemetry'].get('tunnelError')]
    missing = [p['name'] for p in ues if not p['telemetry'].get('interfaces')]
    add('tunnels', 'UE 隧道接口', not missing and bool(ues),
        '、'.join(missing) if missing else f'{len(ues)} 个 UE 均观测到隧道接口；注册、会话和端到端连通需另验', unknown=bool(errors))
    unavailable = [p['name'] for p in pods if p['phase'] == 'Running' and
                   ('rxBytes' not in p['telemetry'] or 'txBytes' not in p['telemetry'] or
                    not fresh(p['telemetry'].get('at'), data['observedAt']))]
    add('telemetry', '流量计数采集', not unavailable,
        '缺失或超过 120 秒：' + '、'.join(unavailable) if unavailable else 'Running Pod 的 eth0 计数读取成功且未超过 120 秒；接口总量不可当作逐流证据', unknown=bool(unavailable))
    proxy_unavailable = [p['name'] for p in pods if p['component'] == 'Envoy' and not p['telemetry'].get('envoy')]
    add('proxy', '代理观测', not proxy_unavailable and bool(data['routes']),
        '、'.join(proxy_unavailable) if proxy_unavailable else f'{len(data["routes"])} 条配置路径，Envoy cluster 计数可读',
        unknown=bool(proxy_unavailable) or 'proxyConfig' in data['errors'])
    def metric_available(resource):
        at = resource.get('metricsAt')
        return resource['cpuUsed'] is not None and resource['memoryUsed'] is not None and fresh(at, data['observedAt'])
    metrics_missing = any(not metric_available(n) for n in nodes) or any(not metric_available(p) for p in active)
    add('metrics', 'CPU / 内存采集', not metrics_missing, '部分资源指标缺失或超过 120 秒' if metrics_missing else 'metrics-server 指标可读，采样未超过 120 秒',
        unknown=metrics_missing)
    access_pods = [p for p in pods if p['component'] in ('UE1', 'UE2', 'UPF')]
    missing_access = [p['name'] for p in access_pods if not interface_observation(p) or
                      not compatible_windows([interface_observation(p)], data['observedAt'])]
    add('activity', '前段端点活动观测', not missing_access and bool(access_pods),
        '等待有效双次采样或接口缺失：' + '、'.join(missing_access) if missing_access else
        'UE TUN / UPF IP 接口计数可用；前段动画是端点关联，不是逐链路实测',
        unknown=bool(missing_access) or not access_pods)
    return rows
