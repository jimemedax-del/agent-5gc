#!/usr/bin/env python3
"""Independent raw-evidence audit of C1 downlink competition (no cluster access)."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import median

ROOT = Path(__file__).resolve().parent.parent


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def require(value, message):
    if not value:
        raise ValueError(message)


def identities(state):
    require({n: len(state[n]) for n in ['free5gc', 'cnf-lab', 'cnf-multi-ue', 'cnf-contention']} ==
            {'free5gc': 16, 'cnf-lab': 3, 'cnf-multi-ue': 5, 'cnf-contention': 1}, 'Unexpected Pod set')
    result = {}
    for ns, pods in state.items():
        for name, pod in pods.items():
            require(pod['phase'] == 'Running' and pod['containers'] and all(
                c['ready'] and c['restartCount'] == 0 and 'running' in c['state'] for c in pod['containers']), 'Unhealthy/restarted Pod')
            result[ns, name] = (pod['uid'], tuple((c['name'], c['containerID'], c['imageID']) for c in pod['containers']))
    return result


def classes(raw):
    return {c['handle']: c for c in raw['class']}


def parse_tc_native(qdisc, class_text, filter_text):
    """iproute2 5.9 emits native HTB text and malformed JSON for u32.

    Preserve the native streams and parse only the fixed HTB/u32 subset used
    by this fixture. Unknown/nonempty formats fail rather than becoming empty.
    """
    parsed_classes, parsed_filters = [], []
    for block in re.split(r'(?=^class )', class_text, flags=re.M):
        if not block.strip():
            continue
        if block.startswith('class fq_codel '):
            # Virtual per-flow queues are counters, not configured HTB classes.
            continue
        head = re.match(r'class htb (\S+) (root|parent (\S+))', block)
        rates = [re.search(r'\b' + key + r' ([\d.]+)([KMG]?bit)\b', block) for key in ['rate', 'ceil']]
        sent = re.search(r'Sent (\d+) bytes (\d+) pkt', block)
        require(head and all(rates) and sent, 'Unknown HTB native output')
        options = {key: float(match[1]) * {'bit': 1, 'Kbit': 1000, 'Mbit': 1000000, 'Gbit': 1000000000}[match[2]] / 8
                   for key, match in zip(['rate', 'ceil'], rates)}
        prio = re.search(r'\bprio (\d+)', block)
        options['prio'] = int(prio[1]) if prio else 0
        parsed_classes.append({'kind': 'htb', 'handle': head[1], 'parent': head[3] if head[3] else '1:',
                               'options': options, 'bytes': int(sent[1]), 'packets': int(sent[2])})
    for block in re.split(r'(?=^filter )', filter_text, flags=re.M):
        if not block.strip():
            continue
        require(block.startswith('filter parent 1: protocol ip pref '), 'Unknown native classifier')
        target = re.search(r'\bflowid (\S+)', block)
        if not target:
            continue
        pref = re.search(r'\bpref (\d+)', block)
        matches = [(v, mask, int(offset)) for v, mask, offset in re.findall(
            r'match ([0-9a-f]+)/([0-9a-f]+) at (\d+)', block)]
        require(pref and len(matches) == 2, 'Incomplete u32 classifier')
        parsed_filters.append({'kind': 'u32', 'parent': '1:', 'protocol': 'ip', 'pref': int(pref[1]),
                               'options': {'flowid': target[1], 'matches': matches}})
    # Round trip through JSON normalizes tuples exactly like saved evidence.
    return json.loads(json.dumps({'qdisc': qdisc, 'class': parsed_classes, 'filter': parsed_filters}))


def tc_shape(state):
    return {'qdisc': [{key: q[key] for key in ['kind', 'handle', 'parent', 'root', 'options'] if key in q}
                      for q in state['qdisc']],
            'class': [{key: c[key] for key in ['kind', 'handle', 'parent', 'options']} for c in state['class']],
            'filter': state['filter']}


def check_tc(raw, mode):
    if 'raw' in raw:
        parsed = parse_tc_native(json.loads(raw['raw']['qdisc']), raw['raw']['class'], raw['raw']['filter'])
        require(all(raw[k] == v for k, v in parsed.items()), 'Derived TC state differs from native output')
    roots = [q for q in raw['qdisc'] if q.get('root')]
    require(len(roots) == 1, 'Missing/ambiguous root queue')
    if mode == 'none':
        require(roots[0]['kind'] == 'noqueue' and not raw['class'] and not raw['filter'], 'Endpoint not restored to noqueue')
        return
    require(roots[0]['kind'] == 'htb' and roots[0]['handle'] == '1:', 'Wrong shared bottleneck')
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
    if mode != 'shared':
        targets = [f.get('options', {}).get('flowid') for f in raw['filter'] if f.get('options', {}).get('flowid')]
        require(Counter(targets) == {'1:10': 3, '1:20': 3}, 'Wrong flow/probe classifier mapping')
        expected_matches = set()
        for number, port, tos in [('10', 5201, 40), ('20', 5202, 80)]:
            for proto in [6, 17]:
                expected_matches.add(('1:' + number, 10, ((f'{proto << 16:08x}', '00ff0000', 8), (f'{port << 16:08x}', 'ffff0000', 20))))
            expected_matches.add(('1:' + number, 5, (('00010000', '00ff0000', 8), (f'{tos << 16:08x}', '00fc0000', 0))))
        actual_matches = {(f['options']['flowid'], f['pref'], tuple(tuple(m) for m in f['options']['matches'])) for f in raw['filter']}
        require(actual_matches == expected_matches, 'Classifier selector differs from user/probe endpoint')


def percentile(values, fraction):
    require(values, 'No RTT samples')
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def receiver(data, spec):
    require(not data.get('error') and data['start']['test_start']['protocol'] == 'UDP' and
            data['start']['test_start']['reverse'] == 1 and data['start']['test_start']['duration'] == 30 and
            data['start']['test_start']['blksize'] == 1200 and data['start']['target_bitrate'] == 8000000,
            'Wrong UDP load/direction/window')
    full = data['end'].get('sum_received') or data['end']['sum']
    require(full['bytes'] > 0 and full['seconds'] >= 28.5 and full['packets'] > 0 and
            0 <= full['lost_packets'] <= full['packets'], 'Invalid complete receiver measurement')
    intervals = [i['sum'] for i in data['intervals'] if
                 i['sum']['start'] >= spec['steadyStartSeconds'] and i['sum']['end'] <= spec['steadyEndSeconds']]
    require(len(intervals) >= 18 and sum(i['seconds'] for i in intervals) >= 17.5 and
            all(i['bytes'] > 0 and i['packets'] > 0 and 0 <= i['lost_packets'] <= i['packets'] for i in intervals),
            'Missing/nonpositive steady receiver intervals')
    require(all(abs(b['start'] - a['end']) < .1 for a, b in zip(intervals, intervals[1:])), 'Noncontiguous receiver intervals')
    duration = sum(i['seconds'] for i in intervals)
    packets, lost = sum(i['packets'] for i in intervals), sum(i['lost_packets'] for i in intervals)
    return {'steadyMbps': sum(i['bytes'] for i in intervals) * 8 / duration / 1e6,
            'windowMbps': [i['bytes'] * 8 / i['seconds'] / 1e6 for i in intervals],
            'steadySeconds': duration, 'steadyPackets': packets, 'steadyLostPackets': lost,
            'steadyLossPercent': 100 * lost / packets, 'fullReceivedBytes': full['bytes'],
            'fullReceiverMbps': full['bytes'] * 8 / full['seconds'] / 1e6,
            'fullPackets': full['packets'], 'fullLostPackets': full['lost_packets'],
            'cookie': data['start']['cookie']}


def rtt(raw, data):
    ts = data['start']['timestamp']['timesecs']
    samples = [(float(t), float(ms)) for t, ms in re.findall(r'\[([\d.]+)\].*?time[=<]([\d.]+) ms', raw)]
    steady = [ms for t, ms in samples if ts + 6 <= t <= ts + 24]
    require(len(steady) >= 30, 'Insufficient class RTT observations')
    match = re.search(r'(\d+) packets transmitted, (\d+) received', raw)
    require(match and int(match[1]) == 150 and 0 < int(match[2]) <= 150, 'Incomplete RTT probe process')
    return {'samples': len(steady), 'p50Ms': percentile(steady, .5), 'p95Ms': percentile(steady, .95),
            'p99Ms': percentile(steady, .99), 'transmitted': int(match[1]), 'received': int(match[2]),
            'scope': 'class-mapped ICMP RTT; not one-way/application/radio delay guarantee'}


def group(folder, topology, spec):
    summary = read(folder / 'summary.json')
    require(summary['outcome'] == 'captured', 'Failed acquisition group')
    mode = summary['mode']
    begin, end = read(folder / 'tc-after.json'), read(folder / 'tc-end.json')
    check_tc(begin, mode)
    check_tc(end, mode)
    observations, starts, ends = {}, [], []
    for user in spec['users']:
        name = user['name']
        live = topology['users'][name]
        data = read(folder / name / 'iperf.json')
        command = read(folder / name / 'iperf-command.json')
        require(command[:7] == ['kubectl', '-n', 'free5gc', 'exec', live['pod'], '--', 'iperf3'] and
                command[command.index('-B') + 1] == live['ip'] and command[command.index('-c') + 1] == topology['target'] and
                command[command.index('-p') + 1] == str(user['nodePort']) and '-u' in command and '-R' in command and
                command[command.index('-b') + 1] == '8M' and command[command.index('-t') + 1] == '30' and
                command[command.index('-l') + 1] == '1200', 'Wrong UE/load/endpoint command binding')
        require(data['start']['connected'] and all(c['local_host'] == live['ip'] and c['remote_host'] == topology['target'] and
                c['remote_port'] == user['nodePort'] for c in data['start']['connected']), 'Wrong UE receiver socket')
        value = receiver(data, spec)
        correct = (folder / (name + '-server.txt')).read_text(encoding='utf-8')
        other = (folder / (('ue2' if name == 'ue1' else 'ue1') + '-server.txt')).read_text(encoding='utf-8')
        require(value['cookie'] in correct and value['cookie'] not in other and
                ('Accepted connection from 192.168.244.129,' in correct or 'Accepted connection from 10.42.1.24,' in correct),
                'Missing correct server cookie/UPF peer or wrong endpoint hit')
        ping_command = read(folder / name / 'ping-command.json')
        require(ping_command[:6] == ['kubectl', '-n', 'free5gc', 'exec', live['pod'], '--'] and
                ping_command[-1] == topology['contention']['ip'] and ping_command[ping_command.index('-I') + 1] == 'uesimtun0' and
                ping_command[ping_command.index('-Q') + 1] == str(user['pingTos']), 'RTT path/class binding differs')
        value['rtt'] = rtt((folder / name / 'ping.txt').read_text(encoding='utf-8'), data)
        active = [i['sum'] for i in data['intervals'] if i['sum']['bytes'] > 0]
        require(active, 'No active UDP intervals')
        ts = data['start']['timestamp']['timesecs']
        starts.append(ts + active[0]['start'] + 1)
        ends.append(ts + active[-1]['end'])
        if mode not in ['none', 'shared']:
            key = '1:10' if name == 'ue1' else '1:20'
            require(classes(end)[key]['bytes'] - classes(begin)[key]['bytes'] >= value['fullReceivedBytes'],
                    'Class byte counters do not cover user receiver payload')
        observations[name] = value
    overlap = min(ends) - max(starts)
    require(overlap >= spec['minimumOverlapSeconds'], 'Flows are not genuinely concurrent')
    if mode != 'none':
        root = next(q for q in end['qdisc'] if q.get('root'))
        require(root['overlimits'] > 0 and root['bytes'] >= sum(v['fullReceivedBytes'] for v in observations.values()),
                'No active common bottleneck counters')
    samples = read(folder / 'resource-samples.json')
    require(samples and max(v['upfMemoryBytes'] for v in samples) < spec['maximumUpfMemoryBytes'], 'Memory guard evidence missing/exceeded')
    return {'users': observations, 'overlapLowerBoundSeconds': overlap,
            'aggregateSteadyMbps': sum(v['steadyMbps'] for v in observations.values()),
            'upfPeakMemoryBytes': max(v['upfMemoryBytes'] for v in samples)}


def performance(groups, spec):
    limits = spec['acceptance']
    checks = []
    for row in groups:
        mode, users = row['mode'], row['users']
        tests = {}
        if mode == 'none':
            tests['unshapedCapacity'] = all(v['steadyMbps'] >= limits['unshapedMinimumMbpsPerUE'] for v in users.values())
        else:
            tests['activeBottleneckCapacity'] = limits['congestedAggregateMinimumMbps'] <= row['aggregateSteadyMbps'] <= limits['congestedAggregateMaximumMbps']
            tests['receiverCongestionLoss'] = sum(v['steadyLostPackets'] for v in users.values()) > 0
            if mode == 'equal':
                tests['equalPerUserMinimum'] = all(v['steadyMbps'] >= limits['equalMinimumMbpsPerUE'] for v in users.values())
            if mode.startswith(('guarantee', 'priority')):
                high = mode.split('-')[1]
                low = 'ue2' if high == 'ue1' else 'ue1'
                if mode.startswith('guarantee'):
                    for role, name in [('High', high), ('Low', low)]:
                        floor, ceiling = limits['guaranteed' + role + 'MinimumMbps'], limits['guaranteed' + role + 'MaximumMbps']
                        rates = users[name]['windowMbps']
                        tests[role + 'RateBounds'] = floor <= users[name]['steadyMbps'] <= ceiling
                        tests[role + 'WindowViolationFraction'] = sum(v < floor for v in rates) / len(rates) <= limits['maximumWindowViolationFraction']
                else:
                    tests['priorityHighMinimum'] = users[high]['steadyMbps'] >= limits['priorityHighMinimumMbps']
                    tests['priorityLowMinimum'] = users[low]['steadyMbps'] >= limits['priorityLowMinimumMbps']
                    tests['priorityPreference'] = users[high]['steadyMbps'] / users[low]['steadyMbps'] >= limits['priorityMinimumHighLowRatio']
        checks.append({'folder': row['folder'], 'mode': mode, 'checks': tests, 'passed': all(tests.values())})
    return {'passed': all(r['passed'] for r in checks), 'checks': checks,
            'rttAndLossAcceptance': 'measurements only; no application latency/loss SLA supplied or accepted'}


def audit(root):
    meta = read(root / 'metadata.json')
    spec = read(root / 'experiment.json')
    summary = read(root / 'summary.json')
    require(summary['outcome'] == 'captured' and meta['task'] == 'C1' and meta['configVersion'] == 'cnf-contention-v1' and
            meta['command'] in ['pilot', 'measure'] and spec['capacityMbps'] == 10 and spec['offeredMbpsPerUE'] == 8,
            'Wrong/unfinished experiment')
    require(len(meta['sha256LF']) >= 20 and 'scripts/cnf-contention.py' in meta['sha256LF'], 'Missing execution provenance')
    current_differs = []
    for relative, expected in meta['sha256LF'].items():
        require((ROOT / relative).resolve().is_relative_to(ROOT), 'Unsafe provenance path')
        source = root / 'inputs' / relative
        require(hashlib.sha256(source.read_bytes().replace(b'\r\n', b'\n')).hexdigest() == expected, 'Execution input hash mismatch')
        if hashlib.sha256((ROOT / relative).read_bytes().replace(b'\r\n', b'\n')).hexdigest() != expected:
            current_differs.append(relative)
    initial = read(root / 'initial/pods.json')
    identity = identities(initial)
    topology = read(root / 'initial/topology.json')
    require(identities(read(root / 'final/pods.json')) == identity, 'Final Pod identity drift')
    require(topology['contention']['node'] == 'lhm-virtual-machine' and len(topology['contention']['containers']) == 3,
            'No single shared endpoint')
    final_topology = read(root / 'final/topology.json')
    require(topology == final_topology, 'Topology/resource identity changed')
    for phase in ['initial', 'final']:
        require((root / phase / 'qos-switch.txt').read_text().strip() == 'QoS Enable: 0', 'Native policer enabled')
        require('CapEff:\t0000000000001000' in (root / phase / 'capabilities.txt').read_text(), 'Wrong container privileges')
        check_tc(read(root / phase / 'tc.json'), 'none')
        for user in spec['users']:
            live = read(root / phase / 'a-fixture/topology.json')['users'][user['name']]
            require(live['supi'] == user['supi'] and 'dev uesimtun0' in (root / phase / (user['name'] + '-rtt-route.txt')).read_text(),
                    'Wrong subscriber or bypassed RTT path')
    mapping = root / 'probe-mapping'
    for user in spec['users']:
        before, after = classes(read(mapping / (user['name'] + '-before.json'))), classes(read(mapping / (user['name'] + '-after.json')))
        own, other = ('1:10', '1:20') if user['name'] == 'ue1' else ('1:20', '1:10')
        require(after[own]['packets'] - before[own]['packets'] >= 5 and after[other]['packets'] == before[other]['packets'],
                'RTT probes not independently mapped to user classes')
    recorded = read(root / 'groups.json')
    rounds = 3 if meta['command'] == 'measure' else 1
    require(Counter((r['mode'], r['round']) for r in recorded) ==
            Counter([('none', 0), ('none', 0)] + [(mode, n) for n in range(1, rounds + 1) for mode in spec['modes']]),
            'Missing/duplicate experimental groups')
    cookies, observations = [], []
    for row in recorded:
        require(Path(row['folder']).name == row['folder'], 'Unsafe group folder')
        raw = group(root / row['folder'], topology, spec)
        require(all(row[k] == v for k, v in raw.items()), 'Derived group differs from raw measurements')
        require(identities(read(root / row['folder'] / 'guard-pods.json')) == identity, 'Pod identity changed during traffic')
        cookies.extend(u['cookie'] for u in raw['users'].values())
        observations.append({k: row[k] for k in ['folder', 'mode', 'round']} | raw)
    require(len(set(cookies)) == len(cookies), 'Session cookie reused')
    rejection = read(root / 'admission-rejection.json')
    require(rejection['outcome'] == 'rejected' and tc_shape(rejection['before']) == tc_shape(rejection['after']), 'Overcapacity admission changed TC')
    check_tc(read(root / 'restore/tc-after.json'), 'none')
    acceptance = performance(observations, spec)
    aggregate = {mode: {u: {'medianMbps': median(r['users'][u]['steadyMbps'] for r in observations if r['mode'] == mode),
        'pooledLossPercent': 100 * sum(r['users'][u]['steadyLostPackets'] for r in observations if r['mode'] == mode) /
        sum(r['users'][u]['steadyPackets'] for r in observations if r['mode'] == mode),
        'rttP95MsByRound': [r['users'][u]['rtt']['p95Ms'] for r in observations if r['mode'] == mode]}
        for u in ['ue1', 'ue2']} for mode in ['none', *spec['modes']]}
    return {'outcome': 'passed', 'route': 'R2', 'stage': 'R2.S4', 'task': 'C1', 'configVersion': spec['configVersion'],
            'evidenceIntegrity': 'passed', 'networkAcceptance': acceptance, 'groups': len(observations),
            'sessions': len(cookies), 'uniqueCookies': len(set(cookies)), 'modeSummary': aggregate,
            'sourceHashes': len(meta['sha256LF']), 'currentSourcesDifferFromExecution': current_differs,
            'auditorSha256LF': hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
            'all25PodIdentitiesUnchanged': True, 'restoredNoqueue': True,
            'minimumOverlapLowerBoundSeconds': min(r['overlapLowerBoundSeconds'] for r in observations),
            'upfPeakSampledMemoryBytes': max(r['upfPeakMemoryBytes'] for r in observations),
            'scope': spec['scope']}


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
