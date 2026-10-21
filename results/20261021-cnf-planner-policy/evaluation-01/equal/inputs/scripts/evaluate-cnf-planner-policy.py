#!/usr/bin/env python3
"""E3 explicit engineered requests with predeclared outcomes, real VM1 execution."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('evaluated_policy', ROOT / 'scripts/cnf-planner-policy.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
live = tool.live
CASES = [
    {'name': 'equal', 'mode': 'equal', 'minima': [4, 4], 'maxima': [8, 8], 'priorities': ['normal', 'normal']},
    {'name': 'equal-lower-request', 'mode': 'equal', 'minima': [3, 3], 'maxima': [8, 8], 'priorities': ['normal', 'normal']},
    {'name': 'guarantee-ue1', 'mode': 'guarantee-ue1', 'minima': [5, 1.5], 'maxima': [8, 4], 'priorities': ['high', 'low']},
    {'name': 'guarantee-ue2', 'mode': 'guarantee-ue2', 'minima': [1.5, 5], 'maxima': [4, 8], 'priorities': ['low', 'high']},
    {'name': 'missing-priority', 'status': 'needs_clarification', 'errorCode': 'missing_required_targets'},
    {'name': 'stale-session', 'status': 'rejected', 'errorCode': 'session_binding_mismatch'},
    {'name': 'over-capacity', 'status': 'rejected', 'errorCode': 'no_compatible_live_profile'},
    {'name': 'unsupported-loss', 'status': 'rejected', 'errorCode': 'unsupported_target'}
]


def fixture_requests(state, case):
    # This is a declared experiment input constructor, never a production request normalizer.
    base = case if 'mode' in case else CASES[0]
    batch = {'requestVersion': 'cnf-planner-requests-v1', 'batch_id': 'e3-test-' + case['name'],
             'assessmentScope': 'controlled_receiver_steady_mean', 'protocol': 'UDP', 'requests': []}
    for i, (slot, binding) in enumerate(sorted(state['observation']['bindings'].items())):
        batch['requests'].append({'request_id': case['name'] + '-' + slot, 'user_id': binding['supi'], 'session_id': binding['session_id'],
          'requirements': {'direction': 'downlink', 'min_bandwidth_mbps': base['minima'][i], 'max_bandwidth_mbps': base['maxima'][i],
                           'priority': base['priorities'][i], 'valid_for_seconds': 30, 'measurement_window_seconds': 20}})
    if case['name'] == 'missing-priority':
        batch['requests'][0]['requirements'].pop('priority')
    elif case['name'] == 'stale-session':
        batch['requests'][0]['session_id'] = 'deliberately-obsolete-session'
    elif case['name'] == 'over-capacity':
        for request in batch['requests']:
            request['requirements']['min_bandwidth_mbps'] = 6
    elif case['name'] == 'unsupported-loss':
        batch['requests'][0]['requirements']['loss_max_percent'] = 0
    return batch


def evaluate(folder):
    folder.mkdir(parents=True, exist_ok=False)
    live.archive_inputs(folder)
    live.save(folder / 'cases.json', CASES)
    summary = {'route': 'R2', 'stage': 'R2.S4', 'task': 'E3', 'outcome': 'failed', 'startedAt': live.now().isoformat(),
               'scope': 'complete_engineered_requests_choose_existing_C1_downlink_mode', 'cases': [],
               'labels': 'predeclared engineering expectations; not independent business recognition labels'}
    try:
        with tool.Backend() as backend:
            state = tool.inspect_state(backend, folder / 'source-inspection')['state']
        live.save(folder / 'source-state.json', state)
        fixture = folder / 'fixture-requests'
        fixture.mkdir()
        hashes = {}
        for case in CASES:
            path = fixture / (case['name'] + '.json')
            live.save(path, fixture_requests(state, case))
            hashes[path.name] = live.p.file_hash(path)
        live.save(folder / 'request-hashes.json', hashes)
        for case in CASES:
            print(json.dumps({'startingCase': case['name']}), flush=True)
            path = fixture / (case['name'] + '.json')
            batch = live.read(path)
            original = copy.deepcopy(batch)
            value = tool.run(folder / case['name'], batch)
            live.need(batch == original, 'request_modified', 'Caller input changed')
            live.need(value['finalStrictBaselinePassed'] and value['original26PodIdentitiesUnchanged'], 'final_check_failed', case['name'])
            if 'mode' in case:
                live.need(value['outcome'] == 'passed_controlled_targets' and value['selectedMode'] == case['mode'] and value['restoration'] == 'restored',
                          'unexpected_policy_result', case['name'])
            else:
                live.need(value['outcome'] == case['status'] and value['issues'][0]['code'] == case['errorCode'] and not value['mutationAttempted'],
                          'unexpected_rejection', case['name'])
            summary['cases'].append({'name': case['name'], 'outcome': value['outcome'],
                                     'selectedMode': value.get('selectedMode'), 'finalStrictBaselinePassed': True})
            live.save(folder / 'summary.json', summary)
        print(json.dumps({'startingCase': 'legacy-e2-control'}), flush=True)
        live.run(folder / 'legacy-e2-control')
        summary['outcome'] = 'passed_controlled_policy_evaluation'
    except Exception as error:
        summary.update(error=str(error), errorCode=getattr(error, 'code', type(error).__name__))
        raise
    finally:
        summary['endedAt'] = live.now().isoformat()
        live.save(folder / 'summary.json', summary)
        print(json.dumps(summary), flush=True)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    evaluate(args.out)
