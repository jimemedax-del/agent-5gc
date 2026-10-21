#!/usr/bin/env python3
"""Offline E2 audit: replay archived contracts and raw receiver/TC evidence."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def need(value, message):
    if not value:
        raise ValueError(message)


def audit(folder):
    inputs = folder / 'inputs'
    hashes = read(folder / 'input-hashes.json')
    actual = {f.relative_to(inputs).as_posix() for f in inputs.rglob('*')
              if f.is_file() and '__pycache__' not in f.parts}
    need(set(hashes) == actual, 'Archived input inventory differs')
    for relative, expected in hashes.items():
        source = (inputs / relative).resolve()
        need(source.is_relative_to(inputs.resolve()), 'Unsafe provenance path')
        need(hashlib.sha256(source.read_bytes().replace(b'\r\n', b'\n')).hexdigest() == expected,
             'Archived source hash mismatch: ' + relative)
    spec = importlib.util.spec_from_file_location('archived_e2', inputs / 'scripts/cnf-planner-live.py')
    live = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(live)
    state, plan, batch = [read(folder / name) for name in ['live-state.json', 'plan.json', 'requests.json']]
    checked = read(folder / 'execution/validation.json')
    stamp = live.p.instant(checked['checkedAt'])
    observed = read(folder / 'execution/recheck/observation.json')
    # Historical replay is confined to this offline auditor; live CLI has no clock override.
    with patch.object(live, 'now', return_value=stamp), patch.object(live.p, 'clock', return_value=stamp):
        validated = live.validate_plan(plan, batch, state, observed)
    need(checked == validated, 'Saved validation differs from historical replay')
    intent = read(folder / 'execution/write-intent.json')
    execution = read(folder / 'execution.json')
    need(intent['livePlanHash'] == execution['livePlanHash'] == plan['livePlanHash'] and
         intent['endpoint'] == state['observation']['endpoint'] and
         stamp <= live.p.instant(intent['startedAt']) <= live.p.instant(plan['validUntil']) and
         live.p.instant(intent['startedAt']) <= live.p.instant(execution['completedAt']), 'Execution binding/timing differs')
    need((folder / 'execution/tc-batch.txt').read_text(encoding='utf-8').splitlines() == live.c.tc_commands('equal'),
         'Write batch differs from fixed equal policy')
    need(read(folder / 'execution/tc-command.json')['returncode'] == 0, 'TC write failed')
    for relative, mode in [('execution/tc-before.json', 'none'), ('execution/tc-after.json', 'equal'),
                           ('restore/tc-before.json', 'equal'), ('restore/tc-after.json', 'none')]:
        live.check_queue(read(folder / relative), mode)
    need((folder / 'restore/tc-batch.txt').read_text(encoding='utf-8').splitlines() == ['qdisc del dev eth0 root handle 1:'] and
         read(folder / 'restore/tc-command.json')['returncode'] == 0, 'Owned restoration batch failed/differs')
    original = state['observation']
    initial_ids = live.c2.audit.identities(original['pods'])
    for relative in ['initial/observed', 'execution/recheck', 'restore/recheck', 'final/observed']:
        evidence = folder / relative
        observation = read(evidence / 'observation.json')
        need(live.digest(observation) == state['fingerprint'], 'Initial/recheck/restoration/final observation drift')
        need(live.c2.audit.identities(observation['pods']) == initial_ids, 'Pod identity drift')
        tc = read(evidence / 'tc.json')
        live.check_queue(tc, 'none')
        need(observation['queueShape'] == live.auditor.tc_shape(tc) and observation['nativeQos'] == 'QoS Enable: 0',
             'Raw queue/native observation differs')
        live.c2.audit.check_network(read(evidence / 'c2-network.json'), clean=True)
        for slot, binding in observation['bindings'].items():
            user = {'ip': binding['ip'], 'pod': binding['pod'], 'podUID': binding['podUID']}
            need(live.session_record(binding['supi'], user, (evidence / (slot + '-sessions.txt')).read_text(encoding='utf-8')) == binding,
                 'Session listing/binding differs')
    topology = read(folder / 'initial/c1/topology.json')
    need(topology == read(folder / 'final/c1/topology.json'), 'Strict initial/final topology differs')
    for slot, binding in original['bindings'].items():
        user = topology['users'][slot]
        need(all(user[k] == binding[v] for k, v in [('pod', 'pod'), ('podUID', 'podUID'), ('ip', 'ip'), ('supi', 'supi')]),
             'Strict topology/session binding differs')
    verified = live.verify_plan(plan, batch, topology, folder / 'measurement')
    need(verified['status'] == 'passed_controlled_targets' and verified == read(folder / 'verification.json'),
         'Raw receiver audit failed/differs from saved verification')
    guard = read(folder / 'measurement/guard-pods.json')
    need(live.c2.audit.identities(guard) == initial_ids, 'Measurement Pod guard differs')
    measurement = read(folder / 'measurement/summary.json')
    need(live.p.instant(execution['completedAt']) <= live.p.instant(measurement['startedAt']) and
         live.p.instant(measurement['endedAt']) < live.p.instant(state['validUntil']), 'Acquisition outside state horizon')
    summary = read(folder / 'summary.json')
    need(summary['outcome'] == 'passed_controlled_scope' and summary['restoration'] == 'restored' and
         summary['finalStrictBaselinePassed'] is True and read(folder / 'restoration.json')['originalObservedStateMatched'] is True,
         'Run or final restoration incomplete')
    return {'outcome': 'passed_offline_raw_audit', 'scope': summary['scope'], 'livePlanId': plan['livePlanId'],
            'provenanceFiles': len(hashes), 'podCount': len(initial_ids), 'targetComparisons': verified['targetComparisons'],
            'overlapLowerBoundSeconds': verified['observation']['overlapLowerBoundSeconds'],
            'latencyLossGuaranteesAccepted': False, 'historicalValidationAt': checked['checkedAt']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    value = audit(args.folder)
    if args.out:
        args.out.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(value, ensure_ascii=False))


if __name__ == '__main__':
    main()
