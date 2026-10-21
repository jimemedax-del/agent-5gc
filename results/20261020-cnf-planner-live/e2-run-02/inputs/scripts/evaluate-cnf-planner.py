#!/usr/bin/env python3
"""E1 repeatable offline fixture acceptance, not a real network experiment."""
import argparse
import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import platform
import shutil

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / 'infra/cnfs/planner-fixtures.json'
spec = importlib.util.spec_from_file_location('planner', ROOT / 'scripts/cnf-planner.py')
planner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(planner)


def evaluate(fixtures):
    planner.need(fixtures['fixtureVersion'] == 'cnf-planner-fixtures-v1', 'wrong_fixture_version', 'Unknown fixture set')
    cases = fixtures['cases']
    planner.need(cases and len({c['id'] for c in cases}) == len(cases), 'duplicate_cases', 'Empty/duplicate fixture cases')
    records = []
    for case in cases:
        # Expected outputs are kept outside tool inputs.
        state = planner.inspect_state(copy.deepcopy(case['stateSource']), case['at'])
        plan, validation = None, None
        if state['status'] == 'ok':
            plan = planner.plan_policy(copy.deepcopy(case['batch']), state['state'], case['at'])
            if plan['status'] == 'planned':
                validation = planner.validate_plan(plan['plan'], case['batch'], state['state'], case['at'])
        actual = {'inspectStatus': state['status'], 'planningStatus': plan['status'] if plan else None,
                  'mode': plan['plan']['mode'] if plan and plan['plan'] else None,
                  'validationStatus': validation['status'] if validation else None}
        records.append({'id': case['id'], 'actual': actual, 'expected': case['expected'],
                        'agrees': actual == case['expected'], 'stateResult': state,
                        'planResult': plan, 'validationResult': validation})
    report = {'evaluationVersion': 'cnf-planner-evaluation-v1', 'route': 'R2', 'stage': 'R2.S4', 'task': 'E1',
              'claimScope': 'Agent-authored offline engineering fixtures; no live observations, policies or network SLA measurements',
              'cases': len(records), 'expectationMatches': sum(r['agrees'] for r in records),
              'planned': sum(r['actual']['planningStatus'] == 'planned' for r in records),
              'validOfflinePlans': sum(r['actual']['validationStatus'] == 'valid_offline' for r in records),
              'notPlanned': sum(r['actual']['planningStatus'] != 'planned' for r in records),
              'disagreements': [r['id'] for r in records if not r['agrees']],
              'executionAuthorizations': sum(bool(result['executionAuthorized']) for r in records
                                             for result in [r['stateResult'], r['planResult'], r['validationResult']] if result),
              'EStageFullyAccepted': False, 'clusterAccessed': False}
    return report, records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, default=FIXTURES)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    report, records = evaluate(planner.read_json(args.fixtures))
    catalog = planner.catalogue()
    sources = [ROOT / name for name in catalog['sourceSha256LF']]
    sources += [Path(__file__), args.fixtures, ROOT / 'tests/test_cnf_planner.py',
                ROOT / 'tests/test_evaluate_cnf_planner.py', ROOT / 'scripts/cnf-requirements.py']
    args.out.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for source in sources:
        relative = source.resolve().relative_to(ROOT).as_posix()
        target = args.out / 'inputs' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        hashes[relative] = planner.file_hash(source)
    metadata = {'route': 'R2', 'stage': 'R2.S4', 'task': 'E1', 'toolVersion': 'cnf-planner-tools-v1',
                'capturedAt': datetime.now(timezone.utc).isoformat(), 'platform': platform.platform(),
                'python': platform.python_version(), 'sourceSha256LF': hashes, 'catalogHash': catalog['catalogHash'],
                'clock': 'Each fixture explicitly declares a historical simulation time; no live freshness claim',
                'clusterAccessed': False}
    for name, value in [('metadata.json', metadata), ('report.json', report), ('cases.json', records), ('catalogue.json', catalog)]:
        with (args.out / name).open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
    print(json.dumps({k: report[k] for k in ['cases', 'expectationMatches', 'planned', 'notPlanned', 'disagreements']}))


if __name__ == '__main__':
    main()
