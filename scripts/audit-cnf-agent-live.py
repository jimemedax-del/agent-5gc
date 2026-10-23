#!/usr/bin/env python3
"""Offline F3a audit: extraction sources, one-shot receipts and raw E3 measurements."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.cnf_agent.live_contracts import LiveSession, OperationSnapshot
from services.cnf_agent.live_requirements import assess
from services.cnf_agent.live_tools import LiveTools, operation_result
from services.cnf_agent.live_transport import bundle


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def need(value, message):
    if not value:
        raise ValueError(message)


def audit_operation(folder, session, core):
    run = folder / 'e3'
    hashes = core.provenance(run)
    tool = load('f3a_frozen_e3', run / 'inputs/scripts/cnf-planner-policy.py')
    live = tool.live
    state, batch, plan = [read(run / name) for name in ('live-state.json', 'requests.json', 'plan.json')]
    payload = {'state': state, 'batch': batch, 'plan': plan}
    need(read(folder / 'receipt.json')['payload'] == payload and state == session.observation.state
         and batch == session.plan.batch and plan == session.plan.policy, 'Receipt/session/E3 bindings differ')
    stamp = live.p.instant(plan['innerPlan']['createdAt'])
    with patch.object(live, 'now', return_value=stamp), patch.object(live.p, 'clock', return_value=stamp):
        need(tool.plan_policy(state, batch)['plan'] == plan, 'Frozen planning replay differs')
    initial = read(run / 'initial-state.json')
    need(initial['fingerprint'] == state['fingerprint'], 'Execution preflight drifted')
    core.observation(live, run / 'initial/observed', state)
    core.observation(live, run / 'final/observed', state)
    topology = read(run / 'initial/c1/topology.json')
    need(topology == read(run / 'final/c1/topology.json'), 'Strict topology changed')
    observed = core.observation(live, run / 'execution/recheck', state)
    checked = read(run / 'execution/validation.json')
    stamp = live.p.instant(checked['checkedAt'])
    with patch.object(live, 'now', return_value=stamp), patch.object(live.p, 'clock', return_value=stamp):
        need(tool.validate_plan(plan, batch, state, observed) == checked, 'Lock-time validation replay differs')
    mode = session.plan.selected_mode
    core.command(live, run / 'execution', live.c.tc_commands(mode))
    core.command(live, run / 'restore', ['qdisc del dev eth0 root handle 1:'])
    for relative, queue in [('execution/tc-before.json', 'none'), ('execution/tc-after.json', mode),
                            ('restore/tc-before.json', mode), ('restore/tc-after.json', 'none')]:
        live.check_queue(read(run / relative), queue)
    core.observation(live, run / 'restore/recheck', state)
    execution, intent = read(run / 'execution.json'), read(run / 'execution/write-intent.json')
    need(intent['policyPlanHash'] == execution['policyPlanHash'] == plan['policyPlanHash']
         and intent['endpoint'] == state['observation']['endpoint']
         and intent['targetMode'] == execution['mode'] == mode
         and stamp <= live.p.instant(intent['startedAt']) <= live.p.instant(plan['validUntil'])
         and live.p.instant(intent['startedAt']) <= live.p.instant(execution['completedAt']), 'Write binding/timing differs')
    measured = tool.verify_plan(plan, batch, topology, run / 'measurement')
    need(measured == read(run / 'verification.json'), 'Raw measurement replay differs')
    acquisition = read(run / 'measurement/summary.json')
    need(live.p.instant(execution['completedAt']) <= live.p.instant(acquisition['startedAt'])
         and live.p.instant(acquisition['endedAt']) < live.p.instant(state['validUntil']), 'Measurement horizon differs')
    cleanup = read(run / 'measurement/cleanup.json')
    need(not cleanup['errors'] and len(cleanup['processes']) == 4 and all(
        p['remoteStop']['returncode'] in (0, 1) and p['localReturncode'] == 0 for p in cleanup['processes']),
        'Experiment process cleanup failed')
    need(read(run / 'restoration.json')['originalObservedStateMatched'] is True, 'Restoration differed')
    raw = read(folder / 'result.json')
    need(raw['summary'] == read(run / 'summary.json') and raw['verification'] == measured, 'Worker summary differs')
    snapshot = OperationSnapshot(operation_id=session.operation_id, status='completed', result=raw,
                                 evidence=session.result.evidence)
    result = operation_result(snapshot, session.plan, session.observation)
    need(result == session.result and result.outcome == 'succeeded' and result.restoration == 'restored',
         'Current result gate did not reproduce recorded success')
    return {'provenance_files': len(hashes), 'targetComparisons': measured['targetComparisons']}


def audit(folder):
    frozen = read(folder / 'frozen-cases.json')
    need(hashlib.sha256((ROOT / frozen['file']).read_bytes()).hexdigest() == frozen['sha256'], 'Frozen cases changed')
    need(hashlib.sha256((ROOT / 'services/cnf_agent/prompts/policy_extract.txt').read_bytes()).hexdigest()
         == frozen['prompt_sha256'], 'Frozen prompt changed')
    cases = read(ROOT / frozen['file'])['cases']
    need(len(cases) == frozen['count'], 'Case count differs')
    core = load('f3a_provenance', ROOT / 'scripts/audit-cnf-planner-live-faults.py')
    rows = []
    for case in cases:
        session = LiveSession.model_validate_json((folder / (case['id'] + '-session.json')).read_text(encoding='utf-8'))
        events = [json.loads(line) for line in (folder / (case['id'] + '-events.jsonl')).read_text(encoding='utf-8').splitlines()]
        need(session.messages[0].text == case['message'] and session.usage.model_calls == 1
             and events[-1]['payload'] == session.model_dump(mode='json'), 'Input or final event differs')
        launches = [event for event in events if event['kind'] == 'execute_experiment_started']
        metadata = [event['payload']['metadata'] for event in events if event['kind'] == 'model_extract_policy_returned']
        need(len(metadata) == 1 and metadata[0]['provider'] == 'deepseek'
             and metadata[0]['requested_model'] == 'deepseek-flash', 'Real provider evidence missing')
        row = {'case': case['id'], 'phase': session.phase.value, 'usage': session.usage.model_dump()}
        if case['expected'] in ('equal', 'guarantee-ue1', 'guarantee-ue2'):
            release = session.observation.release_id
            need(bundle(folder / 'releases' / release)[0] == release, 'Immutable worker release differs')
            need(assess(session.requirement, session.messages)[0] == 'ready' and session.phase.value == 'succeeded'
                 and session.plan.selected_mode == case['expected'] and len(launches) == session.usage.experiments == 1
                 and LiveTools.batch(session.requirement, session.observation, session.requirement_version) == session.plan.batch,
                 'Source, policy or one-shot execution differs')
            row.update(operation_id=session.operation_id, mode=session.plan.selected_mode, release_id=release,
                       **audit_operation(folder / 'operations' / session.operation_id, session, core))
        else:
            need(session.phase.value == case['expected'] and not launches
                 and session.operation_id is None and session.usage.experiments == 0, 'Refusal caused an operation')
            row['no_write'] = True
        rows.append(row)
    return {'route': 'R2', 'stage': 'R2.S5', 'task': 'F3a', 'outcome': 'passed_offline_agent_live_audit',
            'scope': 'Agent-authored engineering acceptance; not independent language accuracy or F3b', 'cases': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    value = audit(args.folder)
    if args.out:
        args.out.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(value, ensure_ascii=False))
