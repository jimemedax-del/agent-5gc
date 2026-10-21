#!/usr/bin/env python3
"""Offline E3 replay from frozen requests, policy selection, TC and receiver evidence."""
import argparse
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def need(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def historical(tool, stamp):
    return patch.multiple(tool.live, now=lambda: stamp)


def audit(folder):
    # The current small provenance checker verifies all archived bytes before importing archived code.
    root = Path(__file__).resolve().parent
    core = load('policy_audit_provenance', root / 'audit-cnf-planner-live-faults.py')
    hashes = core.provenance(folder)
    tool = load('frozen_policy_adapter', folder / 'inputs/scripts/cnf-planner-policy.py')
    evaluator = load('frozen_policy_cases', folder / 'inputs/scripts/evaluate-cnf-planner-policy.py')
    live = tool.live
    state = read(folder / 'source-state.json')
    core.observation(live, folder / 'source-inspection/observed', state)
    cases = read(folder / 'cases.json')
    need(cases == evaluator.CASES, 'Engineering expectations differ from frozen source')
    request_hashes = read(folder / 'request-hashes.json')
    need(set(request_hashes) == {case['name'] + '.json' for case in cases}, 'Request inventory differs')
    rows = []
    for case in cases:
        name = case['name']
        path = folder / 'fixture-requests' / (name + '.json')
        batch = read(path)
        need(live.p.file_hash(path) == request_hashes[path.name] and batch == evaluator.fixture_requests(state, case),
             'Explicit input request/hash differs from predeclared fixture')
        run = folder / name
        core.provenance(run, hashes)
        need(batch == read(run / 'requests.json'), 'Request changed between caller and execution')
        initial, bound = read(run / 'initial-state.json'), read(run / 'live-state.json')
        need(initial == bound and initial['fingerprint'] == state['fingerprint'], 'Fixture or bound state changed across runs')
        core.observation(live, run / 'initial/observed', initial)
        core.observation(live, run / 'final/observed', initial)
        topology = read(run / 'initial/c1/topology.json')
        need(topology == read(run / 'final/c1/topology.json'), 'Strict initial/final topology differs')
        summary, planning = read(run / 'summary.json'), read(run / 'planning.json')
        need(summary['finalStrictBaselinePassed'] is True and summary['original26PodIdentitiesUnchanged'] is True,
             'Final strict check failed')
        stamp = live.p.instant(planning['plan']['innerPlan']['createdAt'] if planning['plan'] else initial['observedAt'])
        with historical(tool, stamp), patch.object(live.p, 'clock', return_value=stamp):
            recomputed = tool.plan_policy(bound, batch)
        need(recomputed == planning, 'Frozen planning replay differs')
        if 'mode' not in case:
            need(summary['outcome'] == planning['status'] == case['status'] and planning['plan'] is None and
                 planning['issues'][0]['code'] == case['errorCode'] and summary['mutationAttempted'] is False and
                 summary['applyCompleted'] is False and summary['restoration'] == 'not_required_yet' and
                 not any((run / p).exists() for p in ['execution', 'measurement', 'restore', 'execution.json']),
                 'Rejected/incomplete request caused a write or was reported successful')
            rows.append({'name': name, 'outcome': summary['outcome'], 'issueCode': case['errorCode'], 'noNetworkWrite': True})
            continue
        plan = read(run / 'plan.json')
        need(planning['plan'] == plan and plan['innerPlan']['mode'] == case['mode'], 'Selected policy differs')
        observed = core.observation(live, run / 'execution/recheck', bound)
        checked = read(run / 'execution/validation.json')
        stamp = live.p.instant(checked['checkedAt'])
        with historical(tool, stamp), patch.object(live.p, 'clock', return_value=stamp):
            need(tool.validate_plan(plan, batch, bound, observed) == checked, 'Historical live validation differs')
        intent, execution = read(run / 'execution/write-intent.json'), read(run / 'execution.json')
        mode = case['mode']
        need(intent['policyPlanHash'] == execution['policyPlanHash'] == plan['policyPlanHash'] and
             intent['endpoint'] == bound['observation']['endpoint'] and intent['targetMode'] == execution['mode'] == mode and
             stamp <= live.p.instant(intent['startedAt']) <= live.p.instant(plan['validUntil']) and
             live.p.instant(intent['startedAt']) <= live.p.instant(execution['completedAt']), 'Execution identity/mode/timing differs')
        core.command(live, run / 'execution', live.c.tc_commands(mode))
        live.check_queue(read(run / 'execution/tc-before.json'), 'none')
        live.check_queue(read(run / 'execution/tc-after.json'), mode)
        live.check_queue(read(run / 'restore/tc-before.json'), mode)
        live.check_queue(read(run / 'restore/tc-after.json'), 'none')
        core.command(live, run / 'restore', ['qdisc del dev eth0 root handle 1:'])
        core.observation(live, run / 'restore/recheck', initial)
        need(read(run / 'restoration.json')['originalObservedStateMatched'] is True and summary['restoration'] == 'restored', 'Restoration incomplete')
        measured = tool.verify_plan(plan, batch, topology, run / 'measurement')
        need(measured == read(run / 'verification.json') and measured['status'] == 'passed_controlled_targets' and
             summary['outcome'] == 'passed_controlled_targets' and summary['selectedMode'] == mode and
             summary['mutationAttempted'] is True and summary['applyCompleted'] is True, 'Raw receiver goals failed/differ')
        acquisition = read(run / 'measurement/summary.json')
        need(live.p.instant(execution['completedAt']) <= live.p.instant(acquisition['startedAt']) and
             live.p.instant(acquisition['endedAt']) < live.p.instant(bound['validUntil']), 'Measurement outside observed state horizon')
        need(live.c2.audit.identities(read(run / 'measurement/guard-pods.json')) == live.c2.audit.identities(bound['observation']['pods']),
             'Measurement Pod identities changed')
        cleanup = read(run / 'measurement/cleanup.json')
        need(not cleanup['errors'] and len(cleanup['processes']) == 4 and all(
             p['remoteStop']['returncode'] in [0, 1] and p['localReturncode'] == 0 for p in cleanup['processes']), 'Normal process cleanup failed')
        rows.append({'name': name, 'outcome': summary['outcome'], 'selectedMode': mode,
                     'targetComparisons': measured['targetComparisons'],
                     'overlapLowerBoundSeconds': measured['observation']['overlapLowerBoundSeconds']})
    core.provenance(folder / 'legacy-e2-control', hashes)
    legacy = load('legacy_policy_control', folder / 'inputs/scripts/audit-cnf-planner-live.py').audit(folder / 'legacy-e2-control')
    need(read(folder / 'legacy-e2-control/live-state.json')['fingerprint'] == state['fingerprint'], 'Legacy control fixture differs')
    summary = read(folder / 'summary.json')
    expected_rows = [{'name': row['name'], 'outcome': row['outcome'], 'selectedMode': row.get('selectedMode'), 'finalStrictBaselinePassed': True} for row in rows]
    need(summary['outcome'] == 'passed_controlled_policy_evaluation' and summary['cases'] == expected_rows, 'Evaluation summary differs')
    return {'outcome': 'passed_offline_policy_audit', 'scope': summary['scope'], 'provenanceFilesPerRun': len(hashes),
            'cases': rows, 'legacyE2Control': legacy, 'podCount': len(live.c2.audit.identities(state['observation']['pods'])),
            'businessRecognitionAccepted': False, 'latencyLossGuaranteesAccepted': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    value = audit(args.folder)
    if args.out:
        args.out.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(value, ensure_ascii=False))
