#!/usr/bin/env python3
"""R2.S4/B: bounded Session-AMBR probe using the existing two-UE fixture.

Run on VM1 with its authorized KUBECONFIG. No credentials or authentication
records are read. Only UE2's two sessionAmbr fields can be changed. Normal
exceptions restore the subscription, module switch and UE2 PDU session; an
external kill requires inspect + the explicit restore command.
"""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / 'infra/cnfs/qos-probe.json'
loader = importlib.util.spec_from_file_location('multi_ue', ROOT / 'scripts/cnf-multi-ue.py')
a = importlib.util.module_from_spec(loader)
loader.loader.exec_module(a)
m1 = a.m1
require = a.require
COLLECTION = 'subscriptionData.provisionedData.smData'
FIELDS = 'dnnConfigurations.internet.sessionAmbr'
UPF = 'deployment/free5gc-helm-free5gc-upf-upf'
UE2 = 'deployment/cnf-a-ue2'
SUPI = 'imsi-208930000000002'


def config():
    value = json.loads(SPEC.read_text(encoding='utf-8'))
    require(value == {
        'configVersion': 'cnf-qos-probe-v3', 'route': 'R2', 'stage': 'R2.S4', 'task': 'B',
        'subscriber': SUPI, 'servingPlmnId': '20893', 'dnn': 'internet',
        'originalAmbr': {'uplink': '1000 Mbps', 'downlink': '1000 Mbps'},
        'probeAmbr': {'uplink': '2 Mbps', 'downlink': '4 Mbps'},
        'offeredMbps': 8, 'durationSeconds': 15, 'roundsPerDirection': 3,
        'maximumUpfMemoryBytes': 536870912,
        'policySource': 'narrow compare-and-set of UE2 subscription smData; normal UDR/PCF/SMF session establishment',
        'scope': 'Session-AMBR ceiling; no GBR, scheduling, dedicated QoS Flow or radio SLA acceptance'
    }, 'Probe configuration differs from fixed safety/acceptance contract')
    return value


def mongo(code):
    result = m1.kube('-n', 'free5gc', 'exec', 'mongodb-0', '--', 'mongosh', '--quiet',
                     'free5gc', '--eval', code)
    return json.loads(result.stdout)


def subscription():
    # smData contains session policy, never auth keys or sequence counters.
    result = mongo('JSON.stringify(db.getCollection(' + json.dumps(COLLECTION) +
        ').find({ueId:' + json.dumps(SUPI) + '},{_id:0}).toArray())')
    require(len(result) == 1 and result[0]['servingPlmnId'] == '20893' and
            result[0]['singleNssai'] == {'sst': 1, 'sd': '010203'} and
            set(result[0]['dnnConfigurations']) == {'internet'}, 'UE2 subscription scope differs')
    return result[0]


def policy_ambr(record):
    return record['dnnConfigurations']['internet']['sessionAmbr']


def set_ambr(expected, desired):
    require(expected in [config()['originalAmbr'], config()['probeAmbr']] and
            desired in [config()['originalAmbr'], config()['probeAmbr']], 'Unapproved policy rate')
    before = subscription()
    require(policy_ambr(before) == expected, 'Subscription drift; refusing overwrite')
    query = {'ueId': SUPI, 'servingPlmnId': '20893',
             FIELDS + '.uplink': expected['uplink'], FIELDS + '.downlink': expected['downlink']}
    update = {'$set': {FIELDS + '.' + k: v for k, v in desired.items()}}
    result = mongo('JSON.stringify(db.getCollection(' + json.dumps(COLLECTION) + ').updateOne(' +
                   json.dumps(query) + ',' + json.dumps(update) + '))')
    require(result.get('matchedCount') == 1, 'Subscription compare-and-set failed')
    after = subscription()
    expected_record = copy.deepcopy(before)
    expected_record['dnnConfigurations']['internet']['sessionAmbr'] = desired
    require(after == expected_record, 'Unexpected subscription change')
    return {'before': before, 'after': after, 'writeResult': result}


def switch(value=None):
    if value is not None:
        require(value in [0, 1], 'Invalid global QoS switch')
        m1.kube('-n', 'free5gc', 'exec', UPF, '--', 'sh', '-ec',
                'printf "' + str(value) + '\\n" > /proc/gtp5g/qos')
    raw = m1.kube('-n', 'free5gc', 'exec', UPF, '--', 'cat', '/proc/gtp5g/qos').stdout
    match = re.fullmatch(r'QoS Enable: ([01])\s*', raw)
    require(match, 'Cannot read QoS switch')
    actual = int(match[1])
    require(value is None or actual == value, 'QoS switch readback differs')
    return actual


def rules():
    return {kind: json.loads(m1.kube('-n', 'free5gc', 'exec', UPF, '--', './gtp5g-tunnel',
                                    'list', kind).stdout) for kind in ['pdr', 'qer', 'far']}


def bind_rules(raw, ip, ul, dl):
    pdrs = [p for p in raw['pdr'] if p['PDI']['UEAddr'] == ip]
    require(len(pdrs) == 2 and len({p['SEID'] for p in pdrs}) == 1, 'Ambiguous UE/IP/PDR mapping')
    seid = pdrs[0]['SEID']
    require(sum(bool(p['PDI']['FTEID']) for p in pdrs) == 1, 'Missing uplink tunnel PDR')
    qids = set(pdrs[0]['QERID'])
    require(all(set(p['QERID']) == qids for p in pdrs), 'Asymmetric QER references')
    qers = [q for q in raw['qer'] if q['SEID'] == seid and q['ID'] in qids]
    require(len(qers) == len(qids) == 2 and all(q['QFI'] == 1 and q['Gate'] == 0 for q in qers),
            'Missing/open-gate default QFI rules')
    rates = sorted((q['MBR']['UL_Kbps'], q['MBR']['DL_Kbps']) for q in qers)
    require(rates == [(0, 0), (ul, dl)], 'Installed QER rate differs from policy')
    require(all(q['GBR']['UL_Kbps'] == q['GBR']['DL_Kbps'] == 0 for q in qers), 'Unexpected GBR')
    return {'ip': ip, 'seid': seid, 'qfi': 1, 'pdrIds': sorted(p['ID'] for p in pdrs),
            'qerIds': sorted(qids), 'ulKbps': ul, 'dlKbps': dl}


def cli(command):
    return m1.kube('-n', 'free5gc', 'exec', UE2, '--', './nr-cli', SUPI, '-e', command).stdout


def renew(out, label):
    sessions = cli('ps-list')
    ids = re.findall(r'PDU Session(\d+):', sessions)
    require(len(ids) <= 2 and all(x in ['1', '2'] for x in ids), 'Unexpected session set; refusing release')
    if ids:
        m1.save(out, label + '-release.txt', cli('ps-release ' + ' '.join(ids)))
    # The configured initial session is re-established automatically by UERANSIM.
    # Sending an explicit ps-establish here can race and create a second session.
    m1.save(out, label + '-establish.txt', 'Wait for configured initial-session auto-establishment; no explicit ps-establish.\n')
    deadline = time.monotonic() + 45
    old_addresses = set(re.findall(r'address: (10\.60\.[\d.]+)', sessions))
    while True:
        sessions = cli('ps-list')
        address = m1.kube('-n', 'free5gc', 'exec', UE2, '--', 'ip', '-j', '-4', 'addr', 'show',
                          'uesimtun0', required=False)
        new_addresses = set(re.findall(r'address: (10\.60\.[\d.]+)', sessions))
        if (len(re.findall(r'PDU Session\d+:', sessions)) == 1 and 'state: PS-ACTIVE\n' in sessions and
                new_addresses and not new_addresses.intersection(old_addresses) and
                address.returncode == 0 and '10.60.' in address.stdout):
            m1.save(out, label + '-sessions.txt', sessions)
            m1.save(out, label + '-address.json', json.loads(address.stdout))
            return
        require(time.monotonic() < deadline, 'UE2 session/tunnel did not become ready')
        time.sleep(1)


def inspect(out):
    raw = rules()
    result = {'capturedAt': m1.now(), 'qosEnable': switch(), 'subscription': subscription(), 'rules': raw,
              'ue2Sessions': cli('ps-list'), 'ue2Status': cli('status'), 'pods': a.snapshot()}
    m1.save(out, 'inspection.json', result)
    return result


def checkpoint(out, label, ambr, enable):
    folder = out / label
    folder.mkdir()
    result = inspect(folder)
    require(len(re.findall(r'PDU Session\d+:', result['ue2Sessions'])) == 1 and
            'PS-ACTIVE-PENDING' not in result['ue2Sessions'] and len(result['rules']['pdr']) == 4,
            'Expected exactly one active session per UE')
    require(result['qosEnable'] == enable and policy_ambr(result['subscription']) == ambr,
            'Policy or switch differs at checkpoint')
    state, topology = a.verify_fixture(folder / 'fixture')
    rates = [int(ambr[k].split()[0]) * 1000 for k in ['uplink', 'downlink']]
    mapped = {u: bind_rules(result['rules'], item['ip'], *(rates if u == 'ue2' else [1000000, 1000000]))
              for u, item in topology['users'].items()}
    require(mapped['ue1']['seid'] != mapped['ue2']['seid'], 'UE session identifiers overlap')
    m1.save(folder, 'binding.json', mapped)
    return state, topology


def run_flow(out, name, route, direction, number, spec, state, topology):
    """Capture timed TCP without asserting equal endpoint counters/windows.

    With a policer and a TCP proxy, sender buffers can remain at test end.
    Throughput comes from receiver bytes / receiver seconds. Difference in
    endpoint totals is recorded and is never interpreted as UDP loss.
    """
    folder = out / name
    folder.mkdir()
    user = route['user']
    sub = folder / user
    sub.mkdir()
    upf, _ = m1.one_pod(state, 'free5gc', 'free5gc-helm-free5gc-upf-upf')
    a.m1.assert_steady(state, a.snapshot())
    initial = m1.upf_observation(folder, 'before', upf)
    control = {'capturedAt': m1.now(), 'qosEnable': switch(), 'subscription': subscription(), 'rules': rules()}
    m1.save(folder, 'control.json', control)
    started = m1.now()
    args = a.command(topology['users'][user], route, direction, spec, topology['target'])
    m1.save(sub, 'command.json', args)
    job = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    samples = []
    result = {'outcome': 'failed', 'rows': []}
    try:
        deadline = time.monotonic() + 45
        while True:
            try:
                stdout, stderr = job.communicate(timeout=1)
                break
            except subprocess.TimeoutExpired:
                current = a.snapshot()
                m1.save(folder, 'guard-pods.json', current)
                m1.assert_steady(state, current)
                memory = int(m1.kube('-n', 'free5gc', 'exec', upf, '--', 'cat',
                                    '/sys/fs/cgroup/memory.current', timeout=5).stdout)
                samples.append({'time': m1.now(), 'upfMemoryBytes': memory})
                m1.save(folder, 'resource-samples.json', samples)
                require(memory < 536870912 and time.monotonic() < deadline, 'Memory/deadline guard')
        ended = m1.now()
        m1.save(sub, 'iperf.json', stdout)
        m1.save(sub, 'iperf.stderr.txt', stderr)
        require(job.returncode == 0, 'iperf command failed')
        data = json.loads(stdout)
        received = data['end']['sum_received']
        item = {'round': number, 'businessClass': 'core', 'direction': direction, 'user': user,
                'outcome': 'passed', 'startedAt': started, 'trafficEndedAt': ended,
                'cookie': data['start']['cookie'], 'receivedBytes': received['bytes'], 'proxyConnections': 2}
        result = {'outcome': 'passed', 'rows': [item]}
        m1.save(folder, 'summary.json', result)
        row = {'folder': name, 'user': user, 'direction': direction, 'round': number,
               'receivedMbps': received['bits_per_second'] / 1e6, 'cookie': data['start']['cookie']}
        final = m1.upf_observation(folder, 'after', upf)
        require(all(b > x for x, b in zip(m1.path_counters(initial['interfaces']),
                                         m1.path_counters(final['interfaces']))), 'No bidirectional UPF counters')
        audit_loader = importlib.util.spec_from_file_location('qos_audit', ROOT / 'scripts/audit-cnf-qos-probe.py')
        auditor = importlib.util.module_from_spec(audit_loader)
        audit_loader.loader.exec_module(auditor)
        deadline = time.monotonic() + 20
        while True:
            m1.save(folder, 'envoy-access.txt', m1.kube('-n', a.NS, 'logs', topology['envoy']['pod'],
                    '--since-time=' + started).stdout)
            for backend, pod in topology['backends'].items():
                m1.save(folder, backend + '.txt', m1.kube('-n', a.NS, 'logs', pod['pod'],
                        '--since-time=' + started).stdout)
            try:
                auditor.flow(out, row, topology, auditor.identity(state))
                break
            except ValueError:
                require(time.monotonic() < deadline, 'Raw receiver/path/guard validation failed; retain evidence')
                time.sleep(.5)
        m1.assert_steady(state, a.snapshot())
        print(f'{name}: receiver {row["receivedMbps"]:.3f} Mbps', flush=True)
        return row
    except Exception as exc:
        result['outcome'] = 'failed'
        result['error'] = str(exc)
        raise
    finally:
        if job.poll() is None:
            job.terminate()
            m1.kube('-n', 'free5gc', 'exec', topology['users'][user]['pod'], '--',
                    'pkill', '-TERM', '-x', 'iperf3', required=False)
            try:
                stdout, stderr = job.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                job.kill()
                stdout, stderr = job.communicate(timeout=5)
            m1.save(sub, 'iperf.json', stdout)
            m1.save(sub, 'iperf.stderr.txt', stderr)
        m1.save(folder, 'summary.json', result)
        m1.save(folder, 'resource-samples.json', samples)


def flows(out, label, state, topology, count, users=('ue2',)):
    spec, _ = a.config()
    spec.update(durationSeconds=config()['durationSeconds'], offeredMbpsPerUE=config()['offeredMbps'])
    rows = []
    for n in range(1, count + 1):
        for direction in ['uplink', 'downlink']:
            for user in users:
                route = next(r for r in spec['routes'] if r['businessClass'] == 'core' and r['user'] == user)
                name = f'{label}-{n:02d}-{direction}-{user}'
                row = {'epoch': label, **run_flow(out, name, route, direction, n, spec, state, topology)}
                rows.append(row)
                m1.save(out, label + '-flows.json', rows)
    return rows


def selected_logs(out, since):
    # Persist only narrowly selected non-credential diagnostic lines.
    for nf in ['smf', 'pcf', 'upf']:
        text = m1.kube('-n', 'free5gc', 'logs', 'deployment/free5gc-helm-free5gc-' + nf + '-' + nf,
                       '--since-time=' + since, '--tail=3000').stdout
        chosen = [line for line in text.splitlines() if re.search(
            r'Session (Establishment|Deletion)|SMPolicy(Create|Delete)|[Qq][Ee][Rr]|AMBR|[Qq]os|[Qq][Ff][Ii]|[Pp]olicy [Aa]ssociation', line)
            and not re.search(r'token|authorization|permanentkey|opc|password|secret', line, re.I)]
        m1.save(out, nf + '-selected.txt', '\n'.join(chosen) + '\n')


def restore(out):
    spec = config()
    before = subscription()
    current = policy_ambr(before)
    require(current in [spec['probeAmbr'], spec['originalAmbr']], 'Unrecognized policy; inspect before recovery')
    # Disable policing first even if session renewal later fails.
    switch(0)
    m1.save(out, 'restore-subscription.json', set_ambr(current, spec['originalAmbr']))
    renew(out, 'restore')
    return checkpoint(out, 'restored', spec['originalAmbr'], 0)


def probe(out):
    spec = config()
    before, topology = checkpoint(out, 'initial', spec['originalAmbr'], 0)
    rows = flows(out, 'baseline', before, topology, 1)
    started = m1.now()
    try:
        m1.save(out, 'policy-change.json', set_ambr(spec['originalAmbr'], spec['probeAmbr']))
        time.sleep(5)
        old = inspect(out)
        # A subscription edit alone must not be misreported as an online update.
        m1.save(out, 'online-observation.json', old)
        bind_rules(old['rules'], topology['users']['ue2']['ip'], 1000000, 1000000)
        renew(out, 'probe')
        for label, enable, count in [('cap-off', 0, 3), ('cap-on', 1, 3), ('cap-reoff', 0, 3)]:
            switch(enable)
            state, topology = checkpoint(out, label, spec['probeAmbr'], enable)
            m1.assert_steady(before, state)
            rows.extend(flows(out, label, state, topology, count))
            if enable:
                rows.extend(flows(out, 'ue1-control', state, topology, 1, ('ue1',)))
    finally:
        state, topology = restore(out)
        m1.assert_steady(before, state)
        selected_logs(out, started)
    rows.extend(flows(out, 'restored', state, topology, 1, ('ue1', 'ue2')))
    final, _ = checkpoint(out, 'final', spec['originalAmbr'], 0)
    m1.assert_steady(before, final)
    m1.save(out, 'flows.json', rows)
    return {'sessions': len(rows), 'all24PodIdentitiesUnchanged': True,
            'restoredPolicyAndSwitch': True, 'acceptance': 'run independent offline audit'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['inspect', 'probe', 'restore'])
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    config()
    args.out.mkdir(parents=True, exist_ok=False)
    inputs = [SPEC, Path(__file__), ROOT / 'scripts/cnf-multi-ue.py', ROOT / 'scripts/audit-cnf-qos-probe.py',
              a.SPEC, ROOT / 'infra/free5gc-test-subscriber-ue2.json',
              ROOT / 'scripts/create-test-subscriber.sh', ROOT / 'tests/test_cnf_qos_probe.py',
              a.base.PROFILE, ROOT / 'scripts/cnf-profile.py',
              *[ROOT / p for p in a.base.profile()['inputSha256LF']]]
    m1.save(args.out, 'metadata.json', {'route': 'R2', 'stage': 'R2.S4', 'task': 'B',
        'configVersion': config()['configVersion'], 'command': args.command, 'startedAt': m1.now(),
        'sha256LF': {str(p.relative_to(ROOT)): a.m2.sha(p) for p in inputs}})
    m1.save(args.out, 'experiment.json', config())
    result = {'outcome': 'failed', 'command': args.command}
    try:
        if args.command == 'inspect':
            inspect(args.out)
        elif args.command == 'restore':
            restore(args.out)
        else:
            result['evidence'] = probe(args.out)
        result['outcome'] = 'passed'
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
        print('QoS probe ERROR: ' + str(exc), file=sys.stderr)
        sys.exit(1)
