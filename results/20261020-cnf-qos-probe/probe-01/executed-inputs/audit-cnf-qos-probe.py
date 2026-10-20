#!/usr/bin/env python3
"""Offline B audit: reconstruct policy/rule/path/rate/restoration evidence."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def identity(state):
    require([len(state[n]) for n in ['free5gc', 'cnf-lab', 'cnf-multi-ue']] == [16, 3, 5], 'Unexpected Pod set')
    result = {}
    for ns, pods in state.items():
        for name, pod in pods.items():
            require(pod['phase'] == 'Running' and pod['containers'] and
                    all(c['ready'] and c['restartCount'] == 0 for c in pod['containers']), 'Unhealthy/restarted Pod')
            result[(ns, name)] = (pod['uid'], tuple((c['containerID'], c['imageID']) for c in pod['containers']))
    return result


def binding(raw, ip, ul, dl):
    pdr = [p for p in raw['pdr'] if p['PDI']['UEAddr'] == ip]
    require(len(pdr) == 2 and len({p['SEID'] for p in pdr}) == 1 and
            sum(bool(p['PDI']['FTEID']) for p in pdr) == 1, 'UE/IP/SEID mapping differs')
    seid = pdr[0]['SEID']
    ids = set(pdr[0]['QERID'])
    require(all(set(p['QERID']) == ids for p in pdr), 'PDR/QER reference differs')
    qers = [q for q in raw['qer'] if q['SEID'] == seid and q['ID'] in ids]
    require(len(qers) == len(ids) == 2 and all(q['QFI'] == 1 and q['Gate'] == 0 for q in qers),
            'Default QFI/gate rules differ')
    require(sorted((q['MBR']['UL_Kbps'], q['MBR']['DL_Kbps']) for q in qers) == [(0, 0), (ul, dl)],
            'Installed MBR differs from subscription')
    require(all(q['GBR']['UL_Kbps'] == q['GBR']['DL_Kbps'] == 0 for q in qers), 'Unexpected GBR')
    require(all(set(q['PDRIDs']) == {p['ID'] for p in pdr} for q in qers), 'QER reverse references differ')
    return seid


def rate_comparison(rows):
    result = {}
    for direction, cap in [('uplink', 2), ('downlink', 4)]:
        values = {epoch: [r['receivedMbps'] for r in rows if r['direction'] == direction and r['epoch'] == epoch]
                  for epoch in ['cap-off', 'cap-on', 'cap-reoff']}
        require(all(len(v) == 3 for v in values.values()), 'Missing fixed rounds')
        require(all(5.2 <= x <= 9.2 for k in ['cap-off', 'cap-reoff'] for x in values[k]),
                'Off controls did not sustain offered load')
        require(all(cap * .1 <= x <= cap * 1.15 for x in values['cap-on']),
                'On receiver result outside ceiling/continuity bounds')
        require(max(values['cap-on']) / min(values['cap-off'] + values['cap-reoff']) <= .8,
                'Insufficient reversible rate separation')
        result[direction] = {'capMbps': cap, **{k + 'Mbps': v for k, v in values.items()}}
    return result


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def flow(root, row, topology, reference_ids):
    folder = root / row['folder']
    user, direction = row['user'], row['direction']
    data = read(folder / user / 'iperf.json')
    command = read(folder / user / 'command.json')
    target = topology['users'][user]
    port = 31301 if user == 'ue1' else 31302
    require(command[:7] == ['kubectl', '-n', 'free5gc', 'exec', target['pod'], '--', 'iperf3'] and
            command[command.index('-B') + 1] == target['ip'] and
            command[command.index('-c') + 1] == topology['target'] and
            command[command.index('-p') + 1] == str(port) and
            command[command.index('-t') + 1] == '15' and command[command.index('-b') + 1] == '8M' and
            ('-R' in command) == (direction == 'downlink'), 'Wrong UE/command/load binding')
    start, end = data['start'], data['end']
    received = end['sum_received']
    require(not data.get('error') and start['test_start']['duration'] == 15 and
            start['test_start']['reverse'] == int(direction == 'downlink') and
            received['seconds'] >= 14.25 and received['bytes'] >= 16384 and
            start['connected'] and all(c['local_host'] == target['ip'] and c['remote_host'] == topology['target'] and
            c['remote_port'] == port for c in start['connected']), 'Invalid receiver/direction/socket evidence')
    rate = received['bytes'] * 8 / received['seconds'] / 1e6
    require(abs(rate - received['bits_per_second'] / 1e6) < 1e-6 and
            abs(rate - row['receivedMbps']) < 1e-6 and row['cookie'] == start['cookie'], 'Derived rate/cookie differs')
    summary = read(folder / 'summary.json')
    require(summary['outcome'] == 'passed' and len(summary['rows']) == 1, 'Failed path group')
    item = summary['rows'][0]
    require(item['cookie'] == start['cookie'] and item['user'] == user and item['direction'] == direction and
            item['receivedBytes'] == received['bytes'], 'Path row differs from raw flow')
    name = 'cnf-a-core-' + user
    cookie = start['cookie']
    correct = (folder / (name + '.txt')).read_text(encoding='utf-8')
    require(cookie in correct and 'Accepted connection from ' + topology['envoy']['ip'] + ',' in correct,
            'Cookie/Envoy peer missing from correct backend')
    require(all(cookie not in (folder / (other + '.txt')).read_text(encoding='utf-8')
                for other in topology['backends'] if other != name), 'Cookie at wrong backend')
    logs = []
    for line in (folder / 'envoy-access.txt').read_text(encoding='utf-8').splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if (value.get('experiment') == 'cnf-multi-ue-v1' and value.get('test_user') == user and
                stamp(item['startedAt']) <= stamp(value['start']) <= stamp(item['trafficEndedAt'])):
            logs.append(value)
    require(len(logs) == 2 and all(v['flags'] == '-' and v['listener'] == v['cluster'] == name and
            v['business_class'] == 'core' and v['local'].rsplit(':', 1)[-1] == str(15301 if user == 'ue1' else 15302) and
            v['upstream'] == topology['services'][name]['spec']['clusterIP'] + ':5201' for v in logs), 'Wrong proxy route')
    field = 'bytes_sent' if direction == 'downlink' else 'bytes_received'
    require(max(int(v[field]) for v in logs) >= received['bytes'], 'Proxy payload incomplete')
    require(identity(read(folder / 'guard-pods.json')) == reference_ids, 'Pod identity changed during flow')
    samples = read(folder / 'resource-samples.json')
    require(samples and max(s['upfMemoryBytes'] for s in samples) < 536870912, 'Missing/excessive memory samples')
    return rate, max(s['upfMemoryBytes'] for s in samples)


def audit(root):
    require(read(root / 'summary.json')['outcome'] == 'passed', 'Probe did not finish/restoration failed')
    meta = read(root / 'metadata.json')
    require((meta['route'], meta['stage'], meta['task'], meta['configVersion'], meta['command']) ==
            ('R2', 'R2.S4', 'B', 'cnf-qos-probe-v1', 'probe'), 'Wrong probe identity')
    for relative, expected in meta['sha256LF'].items():
        path = (ROOT / relative).resolve()
        require(path.is_relative_to(ROOT), 'Invalid source path')
        actual = hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
        require(actual == expected, 'Source hash mismatch: ' + relative)
    spec = read(root / 'experiment.json')
    require(spec == read(ROOT / 'infra/cnfs/qos-probe.json') and spec['probeAmbr'] ==
            {'uplink': '2 Mbps', 'downlink': '4 Mbps'} and spec['offeredMbps'] == 8, 'Probe inputs differ')
    original = read(root / 'initial/inspection.json')
    reference_ids = identity(original['pods'])
    topologies = {}
    for label, enable, limited in [('initial', 0, False), ('cap-off', 0, True), ('cap-on', 1, True),
                                    ('cap-reoff', 0, True), ('restored', 0, False), ('final', 0, False)]:
        obs = read(root / label / 'inspection.json')
        topology = read(root / label / 'fixture/topology.json')
        topologies[label] = topology
        require(obs['qosEnable'] == enable and identity(obs['pods']) == reference_ids and
                identity(read(root / label / 'fixture/pods.json')) == reference_ids, 'Switch/Pod state drift')
        expected = spec['probeAmbr'] if limited else spec['originalAmbr']
        require(obs['subscription']['dnnConfigurations']['internet']['sessionAmbr'] == expected, 'Wrong policy source')
        seids = []
        for user, item in topology['users'].items():
            require(item['supi'] == 'imsi-20893000000000' + ('1' if user == 'ue1' else '2'), 'Wrong SUPI')
            seids.append(binding(obs['rules'], item['ip'], *([2000, 4000] if user == 'ue2' and limited else [1000000, 1000000])))
        require(len(set(seids)) == 2, 'Cross-user SEID mapping')
    change = read(root / 'policy-change.json')
    expected = json.loads(json.dumps(change['before']))
    expected['dnnConfigurations']['internet']['sessionAmbr'] = spec['probeAmbr']
    require(expected == change['after'] and change['before'] == original['subscription'], 'Non-AMBR subscription changed')
    online = read(root / 'online-observation.json')
    require(online['subscription'] == change['after'] and online['qosEnable'] == 0, 'Missing subscription-only observation')
    binding(online['rules'], topologies['initial']['users']['ue2']['ip'], 1000000, 1000000)
    require(read(root / 'final/inspection.json')['subscription'] == original['subscription'], 'Subscription not exactly restored')
    rows = read(root / 'flows.json')
    expected_counts = {'baseline': 2, 'cap-off': 6, 'cap-on': 6, 'cap-reoff': 6, 'ue1-control': 2, 'restored': 4}
    require(dict(Counter(r['epoch'] for r in rows)) == expected_counts and
            len({r['folder'] for r in rows}) == len(rows) and len({r['cookie'] for r in rows}) == len(rows), 'Missing/duplicate flows')
    shape = Counter((r['epoch'], r['user'], r['direction'], r['round']) for r in rows)
    expected_shape = Counter()
    for epoch, count in expected_counts.items():
        users = ['ue1'] if epoch == 'ue1-control' else ['ue1', 'ue2'] if epoch == 'restored' else ['ue2']
        rounds = 3 if epoch in ['cap-off', 'cap-on', 'cap-reoff'] else 1
        for user in users:
            for direction in ['uplink', 'downlink']:
                for n in range(1, rounds + 1):
                    expected_shape[(epoch, user, direction, n)] += 1
    require(shape == expected_shape, 'Experimental cohort changed')
    peak = 0
    for row in rows:
        require(Path(row['folder']).name == row['folder'], 'Invalid flow folder')
        label = {'baseline': 'initial', 'ue1-control': 'cap-on'}.get(row['epoch'], row['epoch'])
        rate, memory = flow(root, row, topologies[label], reference_ids)
        peak = max(peak, memory)
        if row['epoch'] in ['baseline', 'ue1-control', 'restored']:
            require(5.2 <= rate <= 9.2, 'Baseline/control/restored throughput not recovered')
    return {'outcome': 'passed', 'route': 'R2', 'stage': 'R2.S4', 'task': 'B', 'sessions': len(rows),
            'sourceHashes': len(meta['sha256LF']), 'uniqueCookies': len({r['cookie'] for r in rows}),
            'rateComparison': rate_comparison(rows), 'upfPeakSampledMemoryBytes': peak,
            'all24PodIdentitiesUnchanged': True, 'policyAndSwitchRestored': True,
            'supportedWithinProbe': ['session-establishment Session-AMBR ceiling (UL/DL) with gtp5g policing enabled'],
            'notDemonstrated': ['dedicated QoS Flow/MFBR', 'online PCF policy update', 'GBR guarantee',
                                'priority scheduler', 'delay/loss SLA', 'wire QFI classification'],
            'subscriptionOnlyOnlineUpdate': 'old QER remained after 5 seconds; this is not a PCF notification test'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    value = audit(args.root)
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(value, ensure_ascii=False, indent=2))
