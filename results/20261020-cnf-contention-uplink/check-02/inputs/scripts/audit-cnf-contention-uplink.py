#!/usr/bin/env python3
"""Independent C2 evidence audit: actual server reception and ingress IFB path."""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from statistics import median

ROOT = Path(__file__).resolve().parent.parent
NS, VERSION, DEVICE = 'cnf-contention-uplink', 'cnf-contention-uplink-v1', 'cnfc2ifb0'
loader = importlib.util.spec_from_file_location('audit_c1', ROOT / 'scripts/audit-cnf-contention.py')
base = importlib.util.module_from_spec(loader)
loader.loader.exec_module(base)
read, require, classes = base.read, base.require, base.classes
parse_tc_native = base.parse_tc_native


def check_tc(raw, mode):
    if 'raw' in raw:
        parsed = parse_tc_native(json.loads(raw['raw']['qdisc']), raw['raw']['class'], raw['raw']['filter'])
        require(all(raw[k] == v for k, v in parsed.items()), 'Derived TC state differs from native output')
    roots = [q for q in raw['qdisc'] if q.get('root')]
    require(len(roots) == 1, 'Missing/ambiguous root queue')
    if mode == 'none':
        require(roots[0]['kind'] == 'fq_codel' and roots[0]['handle'] == '0:' and not raw['class'] and not raw['filter'], 'Unshaped IFB root is not the kernel default')
        options = roots[0]['options']
        require(options == {'limit': 10240, 'flows': 1024, 'quantum': 1464, 'target': 4999, 'interval': 99999, 'memory_limit': 33554432, 'ecn': True, 'drop_batch': 64}, 'Default IFB queue drift')
        return
    require(roots[0]['kind'] == 'htb' and roots[0]['handle'] == '1:', 'Wrong shared bottleneck')
    require(roots[0]['options']['default'] == ('0x10' if mode == 'shared' else '0x99') and
            roots[0]['options']['r2q'] == 10, 'Wrong default class/root scheduler')
    cls = classes(raw)
    require(cls['1:1']['options']['rate'] == cls['1:1']['options']['ceil'] == 1250000, 'Capacity is not 10 Mbps')
    if mode == 'shared':
        expected = {'1:10': (10, 10, 0)}
    elif mode == 'equal':
        expected = {key: (4, 8, 0) for key in ['1:10', '1:20']}
    else:
        high = '1:10' if mode.endswith('ue1') else '1:20'
        expected = {key: ((6 if key == high else 2), (8 if key == high else 4), int(key != high))
                    if mode.startswith('guarantee') else (2, 10, int(key != high)) for key in ['1:10', '1:20']}
    if mode != 'shared':
        expected['1:99'] = (.1, 10, 7)
    require(set(cls) == {'1:1', *expected}, 'Unexpected TC class set')
    for key, (rate, ceil, prio) in expected.items():
        value = cls[key]
        options = value['options']
        require(value['kind'] == 'htb' and value['parent'] == '1:1' and
                (options['rate'], options['ceil'], options['prio']) == (rate * 125000, ceil * 125000, prio),
                'Wrong guaranteed rate/ceiling/priority')
        leaves = [q for q in raw['qdisc'] if q.get('parent') == key]
        require(len(leaves) == 1 and leaves[0]['kind'] == 'fq_codel' and leaves[0]['options']['limit'] == 256,
                'Missing/wrong leaf queue')
        leaf_options = leaves[0]['options']
        require(4990 <= leaf_options['target'] <= 5010 and 99990 <= leaf_options['interval'] <= 100010 and
                leaf_options['quantum'] == 1464 and not leaf_options.get('ecn', False), 'Leaf scheduling parameters drifted')
        if 'raw' in raw:
            blocks = [block for block in re.split(r'(?=^class )', raw['raw']['class'], flags=re.M)
                      if block.startswith('class htb ' + key + ' ')]
            require(len(blocks) == 1 and 'quantum 1514 ' in blocks[0] and 'burst 32Kb/' in blocks[0] and
                    'cburst 32Kb/' in blocks[0], 'Class burst/quantum differs across modes')
    if mode != 'shared':
        targets = [f.get('options', {}).get('flowid') for f in raw['filter'] if f.get('options', {}).get('flowid')]
        require(Counter(targets) == {'1:10': 3, '1:20': 3}, 'Wrong flow/probe classifier mapping')
        expected_matches = set()
        for number, port, tos in [('10', 5201, 40), ('20', 5202, 80)]:
            for proto in [6, 17]:
                expected_matches.add(('1:' + number, 10, ((f'{proto << 16:08x}', '00ff0000', 8), (f'{port:08x}', '0000ffff', 20))))
            expected_matches.add(('1:' + number, 5, (('00010000', '00ff0000', 8), (f'{tos << 16:08x}', '00fc0000', 0))))
        actual_matches = {(f['options']['flowid'], f['pref'], tuple(tuple(m) for m in f['options']['matches'])) for f in raw['filter']}
        require(actual_matches == expected_matches, 'Uplink classifier must match destination ports and probe ToS')


def known_queue(value):
    for mode in ['none', 'shared', 'equal', 'guarantee-ue1', 'guarantee-ue2', 'priority-ue1', 'priority-ue2']:
        try:
            check_tc(value, mode)
            return
        except (ValueError, KeyError):
            pass
    raise ValueError('Unknown/drifted IFB queue; refusing replacement')


def check_link(links):
    require(len(links) == 1 and links[0]['ifname'] == DEVICE and links[0].get('ifalias') == VERSION and
            links[0]['linkinfo']['info_kind'] == 'ifb' and links[0]['mtu'] == 1450, 'Foreign/misconfigured IFB link')


def check_ingress(value, allow_empty=False):
    ingress = [q for q in value['eth0Qdisc'] if not q.get('root')]
    require(len(ingress) == 1 and ingress[0]['kind'] == 'ingress' and ingress[0]['handle'] == 'ffff:', 'Wrong ingress hook')
    filters = value['ingressFilters']
    if allow_empty and not filters:
        return
    require(len(filters) == 2 and all(f['protocol'] == 'ip' and f['pref'] == 45000 and
            f['kind'] == 'matchall' and f['chain'] == 0 for f in filters), 'Wrong/extra ingress filters')
    options = [f['options'] for f in filters if 'options' in f]
    require(len(options) == 1 and options[0]['handle'] == 1 and len(options[0]['actions']) == 1, 'Ambiguous ingress action')
    action = options[0]['actions'][0]
    require(action['kind'] == 'mirred' and action['mirred_action'] == 'redirect' and action['direction'] == 'egress' and
            action['to_dev'] == DEVICE and action['control_action']['type'] == 'stolen', 'Ingress is not redirected to the owned IFB')


def ingress_bytes(value):
    return next(f['options']['actions'][0]['stats']['bytes'] for f in value['ingressFilters'] if 'options' in f)


def check_network(value, clean=False):
    roots = [q for q in value['eth0Qdisc'] if q.get('root')]
    require(len(roots) == 1 and roots[0]['kind'] == 'noqueue' and roots[0]['handle'] == '0:', 'Endpoint egress was modified')
    if clean:
        require(len(value['eth0Qdisc']) == 1 and not value['ingressFilters'] and not value['link'] and 'tc' not in value,
                'Ingress/IFB not restored')
        return
    require(len(value['eth0Qdisc']) == 2, 'Unexpected qdisc set')
    check_link(value['link'])
    require('UP' in value['link'][0]['flags'], 'IFB is down')
    check_ingress(value)


def identities(state):
    old = {ns: pods for ns, pods in state.items() if ns != NS}
    result = base.identities(old)
    require(len(state.get(NS, {})) == 1, 'Unexpected C2 Pod set')
    for name, pod in state[NS].items():
        require(pod['phase'] == 'Running' and len(pod['containers']) == 3 and all(c['ready'] and
                c['restartCount'] == 0 and 'running' in c['state'] for c in pod['containers']), 'C2 unhealthy/restarted')
        result[NS, name] = (pod['uid'], tuple((c['name'], c['containerID'], c['imageID']) for c in pod['containers']))
    return result


def check_probe_mapping(before, after, name):
    for value in [before, after]:
        check_network(value)
        check_tc(value['tc'], 'equal')
    b, e = classes(before['tc']), classes(after['tc'])
    own, other = ('1:10', '1:20') if name == 'ue1' else ('1:20', '1:10')
    require(e[own]['packets'] - b[own]['packets'] >= 5 and e[other]['packets'] == b[other]['packets'], 'Probe/user class mapping failed')


def receiver(client, spec):
    require(not client.get('error') and isinstance(client.get('server_output_json'), dict), 'Missing server receiver JSON')
    server = client['server_output_json']
    require(not server.get('error'), 'Server-side test error')
    for value in [client, server]:
        start = value['start']['test_start']
        require(start['protocol'] == 'UDP' and start['reverse'] == 0 and start['duration'] == 30 and start['blksize'] == 1200,
                'Wrong direction/protocol/load')
    require(client['start']['cookie'] == server['start']['cookie'], 'Client/server cookie mismatch')
    intervals = [i['sum'] for i in server['intervals']]
    require(intervals and intervals[0]['start'] <= .1 and intervals[-1]['end'] >= 28.5 and
            all(i['sender'] is False and i['bytes'] >= 0 and 0 <= i['lost_packets'] <= i['packets'] and
                i['bytes'] == (i['packets'] - i['lost_packets']) * 1200 for i in intervals), 'Server receiver datagram/byte counts do not close')
    require(all(abs(b['start'] - a['end']) < .1 for a, b in zip(intervals, intervals[1:])), 'Missing receiver intervals')
    selected = [i for i in intervals if i['start'] >= spec['steadyStartSeconds'] and i['end'] <= spec['steadyEndSeconds']]
    require(len(selected) >= 18 and sum(i['seconds'] for i in selected) >= 17.5 and all(i['bytes'] > 0 for i in selected),
            'Insufficient steady receiver windows')
    sent = [i['sum'] for i in client['intervals']]
    require(sent and sent[0]['start'] <= .1 and sent[-1]['end'] >= 28.5 and all(i['sender'] is True and
            i['bytes'] > 0 and i['bytes'] == i['packets'] * 1200 for i in sent), 'Missing/invalid actual sender intervals')
    require(all(abs(b['start'] - a['end']) < .1 for a, b in zip(sent, sent[1:])), 'Missing sender intervals')
    sender_steady = [i for i in sent if i['start'] >= spec['steadyStartSeconds'] and i['end'] <= spec['steadyEndSeconds']]
    require(len(sender_steady) >= 18 and sum(i['seconds'] for i in sender_steady) >= 17.5, 'Insufficient offered-load windows')
    offered = sum(i['bytes'] for i in sender_steady) * 8 / sum(i['seconds'] for i in sender_steady) / 1e6
    require(7.5 <= offered <= 8.5, 'Actual offered load differs from the 8 Mbps contract')
    duration = sum(i['seconds'] for i in selected)
    packets, lost = sum(i['packets'] for i in selected), sum(i['lost_packets'] for i in selected)
    return {'steadyMbps': sum(i['bytes'] for i in selected) * 8 / duration / 1e6,
            'windowMbps': [i['bytes'] * 8 / i['seconds'] / 1e6 for i in selected], 'steadySeconds': duration,
            'steadyPackets': packets, 'steadyLostPackets': lost, 'steadyLossPercent': 100 * lost / packets,
            'fullReceivedBytes': sum(i['bytes'] for i in intervals),
            'fullReceiverMbps': sum(i['bytes'] for i in intervals) * 8 / sum(i['seconds'] for i in intervals) / 1e6,
            'fullPackets': sum(i['packets'] for i in intervals), 'fullLostPackets': sum(i['lost_packets'] for i in intervals),
            'actualSenderSteadyMbps': offered, 'cookie': client['start']['cookie'],
            'receiverBasis': 'server receiver intervals; bytes=(packets-lost)*1200; client intervals are sender load only'}


def active_window(data):
    positive = [i['sum'] for i in data['intervals'] if i['sum']['bytes'] > 0]
    require(positive, 'No actual traffic')
    ts = data['start']['timestamp']['timesecs']
    return ts + positive[0]['start'] + 1, ts + positive[-1]['end']


def group(folder, topology, spec):
    summary = read(folder / 'summary.json')
    require(summary['outcome'] == 'captured', 'Incomplete acquisition group')
    mode = summary['mode']
    require(mode in ['none', *spec['modes']], 'Unknown mode')
    begin, end = read(folder / 'network-start.json'), read(folder / 'network-end.json')
    for value in [begin, end]:
        check_network(value)
        check_tc(value['tc'], mode)
    require(begin['link'][0]['ifindex'] == end['link'][0]['ifindex'], 'IFB replaced during traffic')
    for name in ['qos-before.txt', 'qos-after.txt']:
        require((folder / name).read_text().strip() == 'QoS Enable: 0', 'Native UPF policer drift')
    users, send_windows, receive_windows = {}, [], []
    for user in spec['users']:
        name, live = user['name'], topology['users'][user['name']]
        data = read(folder / name / 'iperf.json')
        command = read(folder / name / 'iperf-command.json')
        require(command[:7] == ['kubectl', '-n', 'free5gc', 'exec', live['pod'], '--', 'iperf3'] and
                command[command.index('-B') + 1] == live['ip'] and command[command.index('-c') + 1] == topology['target'] and
                command[command.index('-p') + 1] == str(user['nodePort']) and '-u' in command and '-R' not in command and
                command[command.index('-b') + 1] == '8M' and command[command.index('-l') + 1] == '1200' and
                command[command.index('-t') + 1] == '30', 'Wrong uplink UE/load/target command')
        require(data['start']['connected'] and all(c['local_host'] == live['ip'] and c['remote_host'] == topology['target'] and
                c['remote_port'] == user['nodePort'] for c in data['start']['connected']), 'Wrong UE sender socket')
        value = receiver(data, spec)
        server = data['server_output_json']
        require(server['start']['connected'] and all(c['local_host'] == topology['uplink']['ip'] and c['local_port'] == user['port'] and
                c['remote_host'] in ['192.168.244.129', '10.42.1.24'] for c in server['start']['connected']), 'Wrong receiver socket/UPF peer')
        correct = (folder / (name + '-server.txt')).read_text(encoding='utf-8')
        other = (folder / (('ue2' if name == 'ue1' else 'ue1') + '-server.txt')).read_text(encoding='utf-8')
        require(value['cookie'] in correct and value['cookie'] not in other, 'Wrong/missing backend cookie')
        ping = read(folder / name / 'ping-command.json')
        require(ping[:6] == ['kubectl', '-n', 'free5gc', 'exec', live['pod'], '--'] and
                ping[-1] == topology['uplink']['ip'] and ping[ping.index('-I') + 1] == 'uesimtun0' and
                ping[ping.index('-Q') + 1] == str(user['pingTos']), 'Probe bypasses user tunnel/class')
        value['rtt'] = base.rtt((folder / name / 'ping.txt').read_text(encoding='utf-8'), data)
        send_windows.append(active_window(data))
        receive_windows.append(active_window(server))
        if mode not in ['none', 'shared']:
            key = '1:10' if name == 'ue1' else '1:20'
            require(classes(end['tc'])[key]['bytes'] - classes(begin['tc'])[key]['bytes'] >= value['fullReceivedBytes'],
                    'Class counters do not cover actual receiver payload')
        users[name] = value
    overlap = {name: min(w[1] for w in windows) - max(w[0] for w in windows)
               for name, windows in [('sender', send_windows), ('receiver', receive_windows)]}
    require(min(overlap.values()) >= spec['minimumOverlapSeconds'], 'Uplink flows are not genuinely concurrent')
    payload = sum(v['fullReceivedBytes'] for v in users.values())
    require(ingress_bytes(end) - ingress_bytes(begin) >= payload, 'Ingress redirect did not cover the received traffic')
    root_b, root_e = (next(q for q in v['tc']['qdisc'] if q.get('root')) for v in [begin, end])
    require(root_e['bytes'] - root_b['bytes'] >= payload, 'Shared IFB root did not carry receiver payload')
    if mode != 'none':
        require(root_e['overlimits'] > root_b['overlimits'], 'No active shared uplink bottleneck')
    samples = read(folder / 'resource-samples.json')
    require(samples and max(v['upfMemoryBytes'] for v in samples) < spec['maximumUpfMemoryBytes'], 'Memory guard missing/exceeded')
    def counters(label):
        lines = (folder / ('upf-' + label + '-interfaces.txt')).read_text().splitlines()
        fields = next(line for line in lines if line.strip().startswith('upfgtp:')).split(':', 1)[1].split()
        return int(fields[0]), int(fields[8])
    require(all(y > x for x, y in zip(counters('before'), counters('after'))), 'No bidirectional UPF traffic')
    return {'users': users, 'overlapLowerBoundSeconds': min(overlap.values()), 'senderOverlapLowerBoundSeconds': overlap['sender'],
            'receiverOverlapLowerBoundSeconds': overlap['receiver'], 'aggregateSteadyMbps': sum(v['steadyMbps'] for v in users.values()),
            'upfPeakMemoryBytes': max(v['upfMemoryBytes'] for v in samples)}


def network_shape(value):
    return {'linkIndex': value['link'][0]['ifindex'], 'linkAlias': value['link'][0]['ifalias'],
            'tc': base.tc_shape(value['tc']), 'redirectTarget': next(f['options']['actions'][0]['to_dev']
            for f in value['ingressFilters'] if 'options' in f)}


def audit(root):
    meta, spec, summary = (read(root / name) for name in ['metadata.json', 'experiment.json', 'summary.json'])
    require(meta['task'] == 'C2' and meta['configVersion'] == VERSION and meta['command'] in ['pilot', 'measure'] and
            summary['outcome'] == 'captured', 'Wrong/incomplete C2 experiment')
    require(len(meta['sha256LF']) >= 27 and all(p in meta['sha256LF'] for p in
            ['scripts/cnf-contention-uplink.py', 'scripts/audit-cnf-contention-uplink.py', 'infra/cnfs/contention-uplink-experiment.json']),
            'Missing execution provenance')
    differences = []
    for relative, expected in meta['sha256LF'].items():
        require((ROOT / relative).resolve().is_relative_to(ROOT), 'Unsafe source path')
        archived = root / 'inputs' / relative
        require(hashlib.sha256(archived.read_bytes().replace(b'\r\n', b'\n')).hexdigest() == expected, 'Execution input hash mismatch')
        if hashlib.sha256((ROOT / relative).read_bytes().replace(b'\r\n', b'\n')).hexdigest() != expected:
            differences.append(relative)
    require(spec == read(root / 'inputs/infra/cnfs/contention-uplink-experiment.json') and spec['direction'] == 'uplink' and
            spec['queueDevice'] == DEVICE and spec['capacityMbps'] == 10 and spec['offeredMbpsPerUE'] == 8, 'Frozen experiment mismatch')
    identity = identities(read(root / 'initial/pods.json'))
    require(identity == identities(read(root / 'final/pods.json')), 'Pod identity changed')
    topology = read(root / 'initial/topology.json')
    require(topology == read(root / 'final/topology.json') and topology['uplink']['node'] == 'lhm-virtual-machine' and
            len(topology['uplink']['containers']) == 3, 'Endpoint/resources changed')
    require(topology['users'] == read(root / 'initial/c1/topology.json')['users'] and
            all(u['node'] == 'ran-test' for u in topology['users'].values()), 'UE identity/clock mismatch')
    for phase in ['initial', 'final']:
        check_network(read(root / phase / 'network.json'), clean=True)
        base.check_tc(read(root / phase / 'c1/tc.json'), 'none')
        require('CapEff:\t0000000000001000' in (root / phase / 'capabilities.txt').read_text(), 'Wrong capabilities')
        require((root / phase / 'c1/qos-switch.txt').read_text().strip() == 'QoS Enable: 0', 'Native QoS enabled')
        for user in spec['users']:
            require(topology['users'][user['name']]['supi'] == user['supi'] and
                    'dev uesimtun0' in (root / phase / (user['name'] + '-rtt-route.txt')).read_text(), 'Wrong user/tunnel route')
    check_network(read(root / 'prepare/before.json'), clean=True)
    prepared = read(root / 'prepare/after.json')
    check_network(prepared)
    check_tc(prepared['tc'], 'none')
    for user in spec['users']:
        check_probe_mapping(read(root / 'probe-mapping' / (user['name'] + '-before.json')),
                            read(root / 'probe-mapping' / (user['name'] + '-after.json')), user['name'])
    recorded = read(root / 'groups.json')
    rounds = 3 if meta['command'] == 'measure' else 1
    require(Counter((r['mode'], r['round']) for r in recorded) == Counter([('none', 0), ('none', 0)] +
            [(mode, n) for n in range(1, rounds + 1) for mode in spec['modes']]), 'Missing/duplicate experimental groups')
    observations, cookies = [], []
    for row in recorded:
        require(Path(row['folder']).name == row['folder'], 'Unsafe group folder')
        value = group(root / row['folder'], topology, spec)
        require(all(row[k] == v for k, v in value.items()), 'Derived observations differ from raw evidence')
        require(identities(read(root / row['folder'] / 'guard-pods.json')) == identity, 'Pod drift during traffic')
        observations.append({k: row[k] for k in ['folder', 'mode', 'round']} | value)
        cookies.extend(u['cookie'] for u in value['users'].values())
    require(len(cookies) == len(set(cookies)), 'Reused session cookie')
    rejection = read(root / 'admission-rejection.json')
    for value in [rejection['before'], rejection['after']]:
        check_network(value)
        known_queue(value['tc'])
    require(rejection['outcome'] == 'rejected' and network_shape(rejection['before']) == network_shape(rejection['after']),
            'Overcapacity request changed network configuration')
    check_network(read(root / 'restore/after.json'), clean=True)
    acceptance = base.performance(observations, spec)
    aggregate = {mode: {u: {'medianMbps': median(r['users'][u]['steadyMbps'] for r in observations if r['mode'] == mode),
        'pooledLossPercent': 100 * sum(r['users'][u]['steadyLostPackets'] for r in observations if r['mode'] == mode) /
        sum(r['users'][u]['steadyPackets'] for r in observations if r['mode'] == mode),
        'rttP95MsByRound': [r['users'][u]['rtt']['p95Ms'] for r in observations if r['mode'] == mode]}
        for u in ['ue1', 'ue2']} for mode in ['none', *spec['modes']]}
    return {'outcome': 'passed', 'route': 'R2', 'stage': 'R2.S4', 'task': 'C2', 'configVersion': VERSION,
            'evidenceIntegrity': 'passed', 'networkAcceptance': acceptance, 'groups': len(observations), 'sessions': len(cookies),
            'uniqueCookies': len(set(cookies)), 'modeSummary': aggregate, 'sourceHashes': len(meta['sha256LF']),
            'currentSourcesDifferFromExecution': differences,
            'auditorSha256LF': hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
            'all26PodIdentitiesUnchanged': True, 'restoredNoIngressOrIfb': True,
            'minimumOverlapLowerBoundSeconds': min(r['overlapLowerBoundSeconds'] for r in observations),
            'actualSenderSteadyMbpsRange': [min(v['actualSenderSteadyMbps'] for r in observations for v in r['users'].values()),
                                          max(v['actualSenderSteadyMbps'] for r in observations for v in r['users'].values())],
            'upfPeakSampledMemoryBytes': max(r['upfPeakMemoryBytes'] for r in observations), 'scope': spec['scope']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.root)
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))

