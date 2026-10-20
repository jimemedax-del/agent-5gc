#!/usr/bin/env python3
"""D1 synthetic regression evaluation; never a human-labeled accuracy claim."""
import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / 'tests/fixtures/requirements-cases.json'
loader = importlib.util.spec_from_file_location('requirements', ROOT / 'scripts/cnf-requirements.py')
baseline = importlib.util.module_from_spec(loader)
loader.loader.exec_module(baseline)


def projection(value):
    return {'category': value['category']['value'], 'categoryStatus': value['category']['status'],
            'confidence': value['category']['confidence'], 'subject': value['subject'],
            'fields': {k: {'value': v['value'], 'resolution': v['resolution']} for k, v in value['requirements'].items()},
            'status': value['status'], 'clarificationFields': value['clarificationFields'],
            'issues': [{k: v for k, v in issue.items() if k != 'text'} for issue in value['issues']],
            'capabilityReview': value['capabilityReview'], 'planningInputComplete': value['planningInputComplete'],
            'executionAuthorized': value['executionAuthorized'], 'planGenerated': value['planGenerated']}


def evaluate_cases(dataset):
    baseline.require(dataset['datasetVersion'] == 'cnf-requirements-cases-v1' and dataset['humanReviewStatus'] == 'pending',
                     'D1 evaluation only accepts the declared unreviewed development dataset')
    cases = dataset['cases']
    baseline.require(cases and len({c['id'] for c in cases}) == len(cases), 'Duplicate/empty cases')
    families = {}
    for case in cases:
        baseline.require(case['reviewStatus'] == 'pending_human_review' and case['input']['request_id'] == case['id'], 'Wrong case provenance')
        baseline.require(case['split'] in ['development_synthetic', 'challenge_synthetic'], 'Unknown development split')
        families.setdefault(case['scenarioFamily'], set()).add(case['split'])
        expected = case['expected']
        baseline.require(set(expected['fields']) == set(baseline.FIELDS) and
                         set(expected['textTargetFields']) <= set(baseline.FIELDS), 'Incomplete expected field contract')
        baseline.require(expected['category'] in ['interactive', 'streaming', 'bulk', None], 'Unknown draft label')
    baseline.require(all(len(splits) == 1 for splits in families.values()), 'A scenario family crosses development groups')
    records, metamorphic = [], []
    for case in cases:
        # Annotation is never passed to the recognizer.
        prediction = baseline.normalize(copy.deepcopy(case['input']))
        expected = case['expected']
        differences = {k: {'expected': v, 'predicted': prediction['requirements'][k]['value']}
                       for k, v in expected['fields'].items() if prediction['requirements'][k]['value'] != v}
        records.append({'id': case['id'], 'split': case['split'], 'scenarioFamily': case['scenarioFamily'],
                        'prediction': prediction, 'expected': expected,
                        'comparison': {'categoryAgrees': prediction['category']['value'] == expected['category'],
                            'statusAgrees': prediction['status'] == expected['status'],
                            'planningCandidateAgrees': prediction['capabilityReview']['candidateForEPlanning'] == expected['candidateForEPlanning'],
                            'fieldDifferences': differences}})
        modified = copy.deepcopy(case['input'])
        modified['flow'] = {'protocol': 'UDP', 'source_port': 56123, 'destination_port': 31403}
        alternate = copy.deepcopy(case['input'])
        alternate['flow'] = {'protocol': 'TCP', 'source_port': 1234, 'destination_port': 5201}
        metadata_match = projection(prediction) == projection(baseline.normalize(modified)) == projection(baseline.normalize(alternate))
        texts = []
        for port in [5201, 5202]:
            variant = copy.deepcopy(case['input'])
            variant['description'] = f'端口 {port}。' + variant.get('description', '')
            texts.append(projection(baseline.normalize(variant)))
        text_match = texts[0] == texts[1] == projection(prediction)
        observed = copy.deepcopy(case['input'])
        observed['observations'] = {'throughput_mbps': 900, 'rtt_p95_ms': 500, 'loss_percent': 99}
        observation_match = projection(prediction) == projection(baseline.normalize(observed))
        metamorphic.append({'id': case['id'], 'metadataPortAndTransportInvariant': metadata_match,
                            'descriptionPortInvariant': text_match, 'observedMetricsDoNotBecomeTargets': observation_match})

    def metrics(rows):
        total = len(rows)
        known = [r for r in rows if r['expected']['category'] is not None]
        unknown = [r for r in rows if r['expected']['category'] is None]
        specified = [(r, k, v) for r in rows for k, v in r['expected']['fields'].items() if v is not None]
        text = [(r, k) for r in rows for k in r['expected']['textTargetFields'] if r['expected']['fields'][k] is not None]
        correct = sum(r['comparison']['categoryAgrees'] for r in rows)
        confusion = Counter((r['expected']['category'] or 'unknown_or_ambiguous', r['prediction']['category']['value'] or 'abstained') for r in rows)
        hallucinations = [{'id': r['id'], 'field': k, 'predicted': r['prediction']['requirements'][k]['value']}
                          for r in rows for k, v in r['expected']['fields'].items() if v is None and r['prediction']['requirements'][k]['value'] is not None]
        return {'cases': total, 'draftCategoryAgreements': correct, 'draftCategoryAgreementFraction': correct / total if total else None,
                'knownLabelCases': len(known), 'knownLabelRecognized': sum(r['prediction']['category']['value'] is not None for r in known),
                'unknownOrAmbiguousCases': len(unknown), 'unknownOrAmbiguousAbstained': sum(r['prediction']['category']['value'] is None for r in unknown),
                'specifiedFieldAgreements': sum(r['prediction']['requirements'][k]['value'] == v for r, k, v in specified),
                'specifiedFields': len(specified),
                'textTargetFieldAgreements': sum(r['prediction']['requirements'][k]['value'] == r['expected']['fields'][k] for r, k in text),
                'textTargetFields': len(text), 'hallucinatedFieldsAgainstDraft': hallucinations,
                'statusAgreements': sum(r['comparison']['statusAgrees'] for r in rows),
                'planningCandidateAgreements': sum(r['comparison']['planningCandidateAgrees'] for r in rows),
                'confusion': [{'draftLabel': k[0], 'prediction': k[1], 'cases': v} for k, v in sorted(confusion.items())]}

    misplaced = [r['id'] for r in records if not r['comparison']['categoryAgrees'] and r['prediction']['capabilityReview']['candidateForEPlanning']]
    under, over = 0, 0
    for row in records:
        wanted = row['expected']['fields']['min_bandwidth_mbps']
        actual = row['prediction']['requirements']['min_bandwidth_mbps']['value']
        if row['prediction']['capabilityReview']['candidateForEPlanning'] and wanted is not None and actual is not None:
            under += max(0, wanted - actual)
            over += max(0, actual - wanted)
    failures = [r['id'] for r in records if not all(r['comparison'][k] for k in ['categoryAgrees', 'statusAgrees', 'planningCandidateAgrees'])
                or r['comparison']['fieldDifferences']]
    report = {'evaluationVersion': 'cnf-requirements-evaluation-v1', 'route': 'R2', 'stage': 'R2.S4', 'task': 'D1',
              'outcome': 'captured_including_disagreements', 'datasetVersion': dataset['datasetVersion'],
              'claimScope': 'Agent-authored synthetic development checks; no independent real-world accuracy or SLA claim',
              'independentHumanValidation': False, 'labelReviewStatus': dataset['humanReviewStatus'],
              'overall': metrics(records), 'byGroup': {name: metrics([r for r in records if r['split'] == name])
                                                    for name in ['development_synthetic', 'challenge_synthetic']},
              'disagreementCaseIds': failures,
              'metamorphic': {'comparisons': len(metamorphic) * 3,
                  'passed': sum(v for row in metamorphic for k, v in row.items() if k != 'id'),
                  'allPassed': all(v for row in metamorphic for k, v in row.items() if k != 'id')},
              'riskProxies': {'meaning': 'input-constraint loss/overrequest proxies only; no policy applied and no measured network violations',
                  'mislabeledPlanningCandidates': misplaced, 'minimumUnderrequestMbpsAmongCandidates': under,
                  'minimumOverrequestMbpsAmongCandidates': over,
                  'executionAuthorizedOutputs': sum(r['prediction']['executionAuthorized'] for r in records),
                  'plansGenerated': sum(r['prediction']['planGenerated'] for r in records)},
              'resourceConflictScenario': dataset['capacityScenario'], 'clusterAccessed': False}
    return report, records, metamorphic


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=CASES)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    dataset = baseline.read_json(args.cases)
    report, records, metamorphic = evaluate_cases(dataset)
    args.out.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__), ROOT / 'scripts/cnf-requirements.py', baseline.CONTRACT, args.cases,
               ROOT / 'tests/test_cnf_requirements.py', ROOT / 'tests/test_evaluate_cnf_requirements.py',
               *[ROOT / path for path in baseline.contract()['referenceCapabilities']['evidence']]]
    hashes = {}
    for path in sources:
        relative = path.resolve().relative_to(ROOT).as_posix()
        hashes[relative] = hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
        target = args.out / 'inputs' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    metadata = {'route': 'R2', 'stage': 'R2.S4', 'task': 'D1', 'contractVersion': baseline.contract()['contractVersion'],
                'capturedAt': datetime.now(timezone.utc).isoformat(), 'platform': platform.platform(), 'python': platform.python_version(),
                'sourceBasisGitHead': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'sourceSha256LF': hashes, 'datasetLabelReviewStatus': 'pending', 'clusterAccessed': False,
                'metricClaims': 'development only; not a held-out or human-reviewed benchmark'}
    for name, value in [('metadata.json', metadata), ('report.json', report), ('metamorphic.json', metamorphic)]:
        (args.out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    with (args.out / 'predictions.jsonl').open('x', encoding='utf-8') as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
    print(json.dumps({'cases': report['overall']['cases'], 'draftCategoryAgreements': report['overall']['draftCategoryAgreements'],
                      'disagreementCaseIds': report['disagreementCaseIds'], 'metamorphic': report['metamorphic'],
                      'independentHumanValidation': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
