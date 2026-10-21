#!/usr/bin/env python3
"""Offline replay of E2 fault acceptance; failed runs must remain failed."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

CASES = {'expired-plan': 'expired_plan', 'queue-drift': 'live_state_drift',
         'partial-write': 'tc_apply_failed', 'measurement-interrupted': 'acquisition_failed'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def need(value, message):
    if not value:
        raise ValueError(message)


def provenance(folder, expected=None):
    inputs = folder / 'inputs'
    hashes = read(folder / 'input-hashes.json')
    actual = {p.relative_to(inputs).as_posix() for p in inputs.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    need(set(hashes) == actual and (expected is None or hashes == expected), 'Input inventory/version differs')
    for relative, digest in hashes.items():
        source = (inputs / relative).resolve()
        need(source.is_relative_to(inputs.resolve()) and
             hashlib.sha256(source.read_bytes().replace(b'\r\n', b'\n')).hexdigest() == digest,
             'Archived input hash differs: ' + relative)
    return hashes


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def observation(live, folder, state, mode='none', same=True):
    value = read(folder / 'observation.json')
    need((live.digest(value) == state['fingerprint']) == same, 'Observation fingerprint differs from expected scenario')
    tc = read(folder / 'tc.json')
    live.check_queue(tc, mode)
    need(value['queueMode'] == mode and value['queueShape'] == live.auditor.tc_shape(tc) and value['nativeQos'] == 'QoS Enable: 0',
         'Queue/native observation differs from raw evidence')
    live.c2.audit.check_network(read(folder / 'c2-network.json'), clean=True)
    live.c2.audit.identities(value['pods'])
    checks = read(folder / 'process-checks.json')
    need({(p['slot'], p['process']) for p in checks} == {(slot, name) for slot in ['ue1', 'ue2'] for name in ['iperf3', 'ping']} and
         len(checks) == 4 and all(p['returncode'] == 1 and not p['stdout'].strip() and
         p['pod'] == value['bindings'][p['slot']]['pod'] for p in checks), 'Owned/unmanaged test processes remain or checks are incomplete')
    for slot, binding in value['bindings'].items():
        user = {'pod': binding['pod'], 'podUID': binding['podUID'], 'ip': binding['ip']}
        need(live.session_record(binding['supi'], user, (folder / (slot + '-sessions.txt')).read_text(encoding='utf-8')) == binding,
             'Raw session binding differs')
    return value


def replay(live, plan, batch, state, observed, checked_at, expected_error=None):
    stamp = live.p.instant(checked_at)
    with patch.object(live, 'now', return_value=stamp), patch.object(live.p, 'clock', return_value=stamp):
        try:
            value = live.validate_plan(plan, batch, state, observed)
        except live.p.Rejection as error:
            need(expected_error == error.code, 'Historical rejection code differs: ' + error.code)
            return None
    need(expected_error is None, 'Expected rejection was accepted on historical replay')
    return value


def command(live, folder, expected, success=True):
    need((folder / 'tc-batch.txt').read_text(encoding='utf-8').splitlines() == expected, 'TC command scope differs')
    result = read(folder / 'tc-command.json')
    need((result['returncode'] == 0) == success, 'TC command outcome differs')
    if not success:
        need(result['stderr'].strip(), 'Real TC failure stderr missing')


def case_audit(live, case, folder, hashes):
    run = folder / 'run'
    provenance(run, hashes)
    state, plan, batch = [read(run / name) for name in ['live-state.json', 'plan.json', 'requests.json']]
    replay(live, plan, batch, state, state['observation'], plan['innerPlan']['createdAt'])
    initial = observation(live, run / 'initial/observed', state)
    final = observation(live, run / 'final/observed', state)
    need(live.c2.audit.identities(initial['pods']) == live.c2.audit.identities(final['pods']), 'Initial/final Pod identity differs')
    topology = read(run / 'initial/c1/topology.json')
    need(topology == read(run / 'final/c1/topology.json') and topology['users'] == read(run / 'initial/c1/a-fixture/topology.json')['users'],
         'Strict initial/final topology or A bindings differ')
    for slot, binding in initial['bindings'].items():
        need(all(topology['users'][slot][a] == binding[b] for a, b in [('pod', 'pod'), ('podUID', 'podUID'), ('ip', 'ip'), ('supi', 'supi')]),
             'Strict topology/session binding differs')
    summary, error, acceptance = [read(path) for path in [run / 'summary.json', run / 'execution-error.json', folder / 'acceptance.json']]
    expected = CASES[case]
    mutated = case in ['partial-write', 'measurement-interrupted']
    completed = case == 'measurement-interrupted'
    need(summary['outcome'] == 'failed' and summary['errorCode'] == error['errorCode'] == expected and
         summary['mutationAttempted'] is mutated and error['mutationAttempted'] is mutated and
         summary['applyCompleted'] is completed and error['applyCompleted'] is completed and
         summary['finalStrictBaselinePassed'] is True and summary['original26PodIdentitiesUnchanged'] is True,
         'Expected failure was hidden, changed, or final baseline failed')
    need(summary['restoration'] == ('restored' if mutated else 'not_required_yet'), 'Restoration outcome differs')
    need(acceptance == {'case': case, 'outcome': 'passed_expected_failure', 'expectedErrorCode': expected,
                       'actualRunOutcome': 'failed', 'adapterMutationAttempted': mutated, 'adapterApplyCompleted': completed,
                       'fixtureActorMutationAttempted': case == 'queue-drift', 'fixtureActorCleaned': case == 'queue-drift',
                       'finalStrictBaselinePassed': True}, 'Acceptance summary differs from actual failure')
    writes = read(folder / 'writes.json')
    expected_writes = [] if case == 'expired-plan' else (['fixture-install', 'fixture-teardown'] if case == 'queue-drift'
                      else ['run/execution', 'run/restore'])
    need([row['folder'] for row in writes] == expected_writes, 'Unexpected actor/adapter writes')
    for write in writes:
        command(live, folder / write['folder'], write['commands'], success='error' not in write)
    if not mutated:
        rejected = read(run / 'execution/rejection.json')
        observed = observation(live, run / 'execution/recheck', state, 'equal' if case == 'queue-drift' else 'none', same=case != 'queue-drift')
        need(rejected['livePlanHash'] == plan['livePlanHash'] and rejected['errorCode'] == expected and
             rejected['observedFingerprint'] == live.digest(observed), 'Rejected plan/observation binding differs')
        replay(live, plan, batch, state, observed, rejected['checkedAt'], expected)
        need(not (run / 'execution/tc-command.json').exists() and not (run / 'execution/write-intent.json').exists() and
             not (run / 'measurement').exists() and not (run / 'restore').exists(), 'Rejected plan caused an adapter write/workload')
        fault = read(folder / 'fault.json')
        if case == 'expired-plan':
            need(live.p.instant(fault['waitStartedAt']) <= live.p.instant(plan['validUntil']) < live.p.instant(fault['waitEndedAt']) <=
                 live.p.instant(rejected['checkedAt']) and fault['planValidUntil'] == plan['validUntil'] and 0 < fault['waitSeconds'] <= 32,
                 'No evidence of actual wall-time expiration')
        else:
            need({k: v for k, v in observed.items() if k not in ['queueMode', 'queueShape']} ==
                 {k: v for k, v in initial.items() if k not in ['queueMode', 'queueShape']}, 'Queue drift changed another resource')
            need(fault['owner'] == 'exercise_fixture_actor' and fault['adapterMutationAttempted'] is False, 'Injected queue ownership differs')
            command(live, folder / 'fixture-install', live.c.tc_commands('equal'))
            live.check_queue(writes[0]['tcAfter'], 'equal')
            live.check_queue(read(folder / 'fixture-teardown/tc-before.json'), 'equal')
            command(live, folder / 'fixture-teardown', ['qdisc del dev eth0 root handle 1:'])
            live.check_queue(writes[1]['tcAfter'], 'none')
    else:
        observed = observation(live, run / 'execution/recheck', state)
        checked = read(run / 'execution/validation.json')
        need(replay(live, plan, batch, state, observed, checked['checkedAt']) == checked, 'Historical execution validation differs')
        intent = read(run / 'execution/write-intent.json')
        need(intent['livePlanHash'] == plan['livePlanHash'] and intent['endpoint'] == initial['endpoint'] and
             live.p.instant(checked['checkedAt']) <= live.p.instant(intent['startedAt']) <= live.p.instant(plan['validUntil']), 'Write intent/timing differs')
        live.check_queue(read(run / 'execution/tc-before.json'), 'none')
        command(live, run / 'restore', ['qdisc del dev eth0 root handle 1:'])
        live.check_queue(read(run / 'restore/tc-after.json'), 'none')
        observation(live, run / 'restore/recheck', state)
        need(read(run / 'restoration.json')['originalObservedStateMatched'] is True, 'Restoration incomplete')
        if case == 'partial-write':
            first = live.c.tc_commands('equal')[0]
            command(live, run / 'execution', [first, first], success=False)
            partial = writes[0]['tcAfter']
            live.owned_partial(partial)
            need(len(partial['qdisc']) == 1 and not partial['class'] and not partial['filter'] and
                 partial == read(run / 'restore/tc-before.json') and not (run / 'execution.json').exists() and
                 not (run / 'measurement').exists(), 'Partial-write recovery did not start from the actual incomplete root')
        else:
            command(live, run / 'execution', live.c.tc_commands('equal'))
            live.check_queue(read(run / 'restore/tc-before.json'), 'equal')
            fault = read(folder / 'fault.json')
            token = state['liveStateHash'][:24]
            actual = read(run / 'measurement/ue1/iperf-command.json')
            need(fault['command'] == actual and fault['pod'] == initial['bindings']['ue1']['pod'] and
                 fault['rawCmdline'].rstrip('\0').split('\0') == actual[6:] and fault['token'] == token and
                 actual[actual.index('--extra-data') + 1] == token and fault['stop']['returncode'] == 0 and
                 fault['pid'].isdigit(), 'Interrupted process was not the actual tagged receiver')
            measurement = read(run / 'measurement/summary.json')
            need(measurement['outcome'] == 'failed' and not (run / 'verification.json').exists(), 'Interrupted measurement became a successful verification')
            receiver = read(run / 'measurement/ue1/iperf.json')
            need('interrupt' in receiver.get('error', '').lower() and receiver['start']['connected'] and
                 all(v['local_host'] == initial['bindings']['ue1']['ip'] for v in receiver['start']['connected']), 'No raw receiver interruption evidence')
            cleanup = read(run / 'measurement/cleanup.json')
            need(not cleanup['errors'] and len(cleanup['processes']) == 4, 'Incomplete/failed owned process cleanup')
            for row in cleanup['processes']:
                cmd = row['command']
                name = 'ue1' if row['pod'] == initial['bindings']['ue1']['pod'] else 'ue2'
                kind = 'iperf' if cmd[6] == 'iperf3' else 'ping'
                need(cmd == read(run / 'measurement' / name / (kind + '-command.json')) and token in cmd and
                     row['remoteStop']['returncode'] in [0, 1] and row['localReturncode'] is not None,
                     'Owned cleanup command/result differs')
            need(any(row['localReturncode'] != 0 for row in cleanup['processes']), 'No local process failure observed')
    return {'case': case, 'expectedErrorCode': expected, 'actualRunOutcome': 'failed', 'finalBaselineMatched': True,
            'podCount': len(live.c2.audit.identities(initial['pods'])), 'initialFingerprint': state['fingerprint']}


def audit(folder):
    hashes = provenance(folder)
    live = load('archived_fault_live', folder / 'inputs/scripts/cnf-planner-live.py')
    cases = [case_audit(live, case, folder / case, hashes) for case in CASES]
    need(len({row['initialFingerprint'] for row in cases}) == 1, 'Fixture changed across cases')
    provenance(folder / 'normal-control', hashes)
    normal_audit = load('archived_normal_auditor', folder / 'inputs/scripts/audit-cnf-planner-live.py')
    normal = normal_audit.audit(folder / 'normal-control')
    state = read(folder / 'normal-control/live-state.json')
    need(state['fingerprint'] == cases[0]['initialFingerprint'], 'Normal control started from another fixture')
    summary = read(folder / 'summary.json')
    need(summary['outcome'] == 'passed_fault_acceptance' and len(summary['cases']) == 4 and
         [row['case'] for row in summary['cases']] == list(CASES) and
         summary['cases'] == [read(folder / case / 'acceptance.json') for case in CASES], 'Suite summary differs')
    return {'outcome': 'passed_offline_fault_audit', 'scope': summary['scope'], 'provenanceFilesPerRun': len(hashes),
            'cases': cases, 'normalControl': normal, 'podCount': cases[0]['podCount'],
            'limitations': 'Only controlled queue drift, duplicate-root TC failure and tagged receiver interruption; no Pod replacement, network partition, hard kill or power-loss acceptance'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    value = audit(args.folder)
    if args.out:
        args.out.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(value, ensure_ascii=False))
