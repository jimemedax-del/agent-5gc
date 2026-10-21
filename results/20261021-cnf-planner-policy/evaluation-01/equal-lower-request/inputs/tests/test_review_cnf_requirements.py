"""Engineering fixtures only: no test annotation is independent human evidence."""
import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('review_requirements', ROOT / 'scripts/review-cnf-requirements.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def collection():
    return {'collectionVersion': 'cnf-requirements-collection-v1', 'collectionId': 'unit-fixture-not-real-data',
            'cases': [{'id': 'unit-1', 'scenarioFamily': 'unit-fixture-family', 'split': 'development',
                       'origin': {'kind': 'synthetic', 'authorKind': 'agent', 'authorId': 'unit-author',
                                  'sourceRef': 'unit-test fixture only', 'collectedAt': '2026-10-20T00:00:00+08:00'},
                       'input': {'request_id': 'unit-1', 'description': '视频播放'}}]}


def annotate(item, decision='known', category='streaming'):
    # Simulated declarations exercise validation; they are never delivered as real reviews.
    item['review'] = {'decision': decision, 'category': category, 'reason': 'unit-test declaration only',
                      'reviewerId': 'unit-reviewer', 'reviewerKind': 'human',
                      'reviewedAt': datetime.now(timezone.utc).isoformat(), 'independentOfPredictions': True}


def target_labels(item):
    item['targetReview'] = {'status': 'complete', 'fields': {
        f: {'value': None, 'resolution': 'unknown', 'basis': 'not specified in unit input', 'evidence': []}
        for f in tool.baseline.FIELDS}}


class ReviewRequirementsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / 'bundle'
        self.packet = tool.prepare(collection(), self.folder)

    def save(self):
        (self.folder / 'review.json').write_text(
            json.dumps(self.packet, ensure_ascii=False, allow_nan=False), encoding='utf-8')

    def load(self):
        self.save()
        return tool.load_bundle(self.folder / 'review.json')

    def evaluate_refrozen_unit_packet(self):
        """Metric fixtures with changed raw facts need a new freeze, just like CLI data."""
        raw = {'collectionVersion': 'cnf-requirements-collection-v1', 'collectionId': 'unit-refrozen',
               'cases': [{k: v for k, v in i.items() if k not in ['review', 'targetReview']} for i in self.packet['items']]}
        folder = Path(self.temp.name) / 'refrozen'
        packet = tool.prepare(raw, folder)
        for item, annotated in zip(packet['items'], self.packet['items']):
            item['review'] = copy.deepcopy(annotated['review'])
            item['targetReview'] = copy.deepcopy(annotated['targetReview'])
            item['review']['reviewedAt'] = datetime.now(timezone.utc).isoformat()
        (folder / 'review.json').write_text(json.dumps(packet), encoding='utf-8')
        packet, manifest, normalizer = tool.load_bundle(folder / 'review.json')
        return tool.evaluate(packet, manifest, normalizer)

    def test_pending_packet_audits_without_fabricated_score(self):
        packet, manifest, _ = self.load()
        report = tool.audit(packet, manifest)
        self.assertEqual(report['humanCategoryDeclarations'], 0)
        self.assertFalse(report['evaluationReady'])
        self.assertFalse(report['stageDAccepted'])
        self.assertNotIn('accuracy', report)

    def test_unreviewed_category_blocks_evaluation(self):
        packet, manifest, normalizer = self.load()
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            tool.evaluate(packet, manifest, normalizer)

    def test_raw_input_cannot_change_after_preparation(self):
        self.packet['items'][0]['input']['description'] = '后台备份'
        with self.assertRaisesRegex(ValueError, 'changed after freezing'):
            self.load()

    def test_membership_cannot_be_removed_to_hide_failure(self):
        self.packet['items'] = []
        with self.assertRaises(ValueError):
            self.load()

    def test_frozen_baseline_tampering_rejected(self):
        path = self.folder / 'frozen-inputs/scripts/cnf-requirements.py'
        path.write_text('raise RuntimeError("must not execute")', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.load()

    def test_original_collection_tampering_rejected(self):
        (self.folder / 'collection.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'collection changed'):
            self.load()

    def test_changed_human_label_does_not_change_prediction(self):
        annotate(self.packet['items'][0])
        packet, manifest, normalizer = self.load()
        first, first_records = tool.evaluate(packet, manifest, normalizer)
        self.packet['items'][0]['review']['category'] = 'bulk'
        packet, manifest, normalizer = self.load()
        second, second_records = tool.evaluate(packet, manifest, normalizer)
        self.assertEqual(first_records[0]['prediction'], second_records[0]['prediction'])
        self.assertNotEqual(first['disagreements'], second['disagreements'])

    def test_unreviewed_targets_excluded_not_counted_as_unknown(self):
        annotate(self.packet['items'][0])
        packet, manifest, normalizer = self.load()
        report, _ = tool.evaluate(packet, manifest, normalizer)
        metrics = report['strata']['synthetic/development']
        self.assertEqual(metrics['reviewedTargetFields'], 0)
        self.assertEqual(report['unreviewedTargetsNotScored'], 1)

    def test_complete_target_review_requires_all_fields(self):
        annotate(self.packet['items'][0])
        self.packet['items'][0]['targetReview'] = {'status': 'complete', 'fields': {}}
        with self.assertRaisesRegex(ValueError, 'every target'):
            self.load()

    def test_target_unknowns_do_not_reward_empty_value_only(self):
        item = self.packet['items'][0]
        annotate(item)
        target_labels(item)
        item['targetReview']['fields']['priority'].update(resolution='conflict', evidence=[{'path': '/description', 'quote': '视频'}])
        packet, manifest, normalizer = self.load()
        report, records = tool.evaluate(packet, manifest, normalizer)
        field = next(f for f in records[0]['targetComparisons'] if f['field'] == 'priority')
        self.assertTrue(field['valueMatches'])
        self.assertFalse(field['resolutionMatches'])
        self.assertEqual(report['disagreements'], ['unit-1'])

    def test_hallucination_against_reviewed_unknown_counted(self):
        # A deliberate engineering disagreement, not an assertion that this is the correct label.
        raw = collection()
        raw['cases'][0]['input']['description'] = '视频播放，最低 4 Mbps'
        folder = Path(self.temp.name) / 'other'
        packet = tool.prepare(raw, folder)
        annotate(packet['items'][0])
        target_labels(packet['items'][0])
        manifest = tool.read_json(folder / 'manifest.json')
        report, _ = tool.evaluate(packet, manifest, tool.baseline)
        self.assertEqual(report['strata']['synthetic/development']['hallucinatedFields'], 1)

    def test_explicit_target_requires_original_evidence(self):
        item = self.packet['items'][0]
        annotate(item)
        target_labels(item)
        item['targetReview']['fields']['priority'].update(value='high', resolution='explicit')
        with self.assertRaisesRegex(ValueError, 'require evidence'):
            self.load()

    def test_fabricated_quote_rejected(self):
        with self.assertRaisesRegex(ValueError, 'absent'):
            tool.validate_evidence({'path': '/description', 'quote': '最低 9 Mbps'}, {'description': '最低 4 Mbps'})

    def test_structured_evidence_must_match_original(self):
        request = {'requested': {'min_bandwidth_mbps': 4}}
        tool.validate_evidence({'path': '/requested/min_bandwidth_mbps', 'value': 4}, request)
        with self.assertRaises(ValueError):
            tool.validate_evidence({'path': '/requested/min_bandwidth_mbps', 'value': 5}, request)

    def test_agent_review_not_independent_human_label(self):
        annotate(self.packet['items'][0])
        self.packet['items'][0]['review']['reviewerKind'] = 'agent'
        with self.assertRaisesRegex(ValueError, 'Human annotation'):
            self.load()

    def test_prediction_independence_must_be_explicit_boolean(self):
        annotate(self.packet['items'][0])
        self.packet['items'][0]['review']['independentOfPredictions'] = 'true'
        with self.assertRaises(ValueError):
            self.load()

    def test_known_decision_requires_category(self):
        annotate(self.packet['items'][0], category=None)
        with self.assertRaisesRegex(ValueError, 'disagree'):
            self.load()

    def test_review_timestamp_cannot_predate_freeze(self):
        annotate(self.packet['items'][0])
        self.packet['items'][0]['review']['reviewedAt'] = '2026-10-20T00:01:00+08:00'
        with self.assertRaisesRegex(ValueError, 'predates frozen'):
            self.load()

    def test_collection_with_answer_labels_rejected(self):
        value = collection()
        value['cases'][0]['expected'] = {'category': 'streaming'}
        with self.assertRaises(ValueError):
            tool.validate_collection(value, tool.development_reference(), tool.baseline)

    def test_duplicate_id_rejected(self):
        value = collection()
        value['cases'].append(copy.deepcopy(value['cases'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            tool.validate_collection(value, tool.development_reference(), tool.baseline)

    def test_no_synthetic_or_agent_origin_real_holdout(self):
        value = collection()
        value['cases'][0]['split'] = 'holdout'
        with self.assertRaises(ValueError):
            tool.validate_collection(value, tool.development_reference(), tool.baseline)
        value['cases'][0]['origin']['kind'] = 'real_requirement'
        with self.assertRaisesRegex(ValueError, 'Agent-generated'):
            tool.validate_collection(value, tool.development_reference(), tool.baseline)

    def test_d1_description_or_family_cannot_be_reused_as_holdout(self):
        for description, family in [('视频会议', 'new-unit-family'), ('a fresh description', 'conference')]:
            value = collection()
            case = value['cases'][0]
            case.update(split='holdout', scenarioFamily=family)
            case['origin'].update(kind='real_requirement', authorKind='human')
            case['input']['description'] = description
            with self.assertRaisesRegex(ValueError, 'D1'):
                tool.validate_collection(value, tool.development_reference(), tool.baseline)

    def test_family_and_description_cannot_cross_splits(self):
        for same_family in [True, False]:
            value = collection()
            second = copy.deepcopy(value['cases'][0])
            second.update(id='unit-2', split='holdout')
            second['input']['request_id'] = 'unit-2'
            second['origin'].update(kind='real_requirement', authorKind='human')
            if same_family:
                second['input']['description'] = 'new unit scenario'
            else:
                # Keep duplicate text outside D1 phrases to test within-collection detection.
                value['cases'][0]['input']['description'] = 'unique unit wording'
                second['input']['description'] = 'unique unit wording'
                second['scenarioFamily'] = 'other-unit-family'
            value['cases'].append(second)
            with self.assertRaisesRegex(ValueError, 'crosses splits'):
                tool.validate_collection(value, tool.development_reference(), tool.baseline)

    def test_review_author_must_differ_for_holdout(self):
        item = copy.deepcopy(self.packet['items'][0])
        item['split'] = 'holdout'
        annotate(item)
        item['review']['reviewerId'] = item['origin']['authorId']
        with self.assertRaisesRegex(ValueError, 'author'):
            tool.validate_labels(item, tool.baseline)

    def test_coverage_and_refusals_not_hidden_by_selective_accuracy(self):
        item = self.packet['items'][0]
        item['input']['description'] = '未见过的表达'
        annotate(item, category='interactive')
        manifest = tool.read_json(self.folder / 'manifest.json')
        report, _ = self.evaluate_refrozen_unit_packet()
        metrics = report['strata']['synthetic/development']
        self.assertEqual(metrics['knownAccuracyIncludingRefusals'], 0)
        self.assertEqual(metrics['knownCoverage'], 0)
        self.assertIsNone(metrics['selectiveAccuracy'])

    def test_d1_conversion_excludes_all_draft_labels(self):
        converted = tool.from_d1()
        self.assertEqual(len(converted['cases']), 36)
        for case in converted['cases']:
            self.assertNotIn('expected', case)
            self.assertNotIn('labelBasis', case)
            self.assertEqual(case['origin']['kind'], 'synthetic')
            self.assertEqual(case['split'], 'development')

    def test_unknown_and_ambiguous_exactness_separate_from_safe_refusal(self):
        item = self.packet['items'][0]
        item['input']['description'] = '未见过的表达'
        annotate(item, decision='ambiguous', category=None)
        manifest = tool.read_json(self.folder / 'manifest.json')
        report, _ = self.evaluate_refrozen_unit_packet()
        metrics = report['strata']['synthetic/development']
        self.assertEqual(metrics['safeAbstentions'], 1)
        self.assertEqual(metrics['exactDecisionMatches'], 0)

    def test_prepare_will_not_overwrite_existing_bundle(self):
        with self.assertRaises(FileExistsError):
            tool.prepare(collection(), self.folder)

    def test_local_form_does_not_include_predictions_or_preselected_answer(self):
        html = tool.render_review(self.packet)
        embedded = html.split('<script type="application/json" id="data">')[1].split('</script>')[0]
        packet = json.loads(embedded)
        self.assertEqual(packet['items'][0]['review']['decision'], 'unreviewed')
        self.assertNotIn('prediction', packet['items'][0])
        self.assertNotIn('expected', packet['items'][0])
        self.assertIn('URL.createObjectURL', html)

    def test_local_form_escapes_embedded_script_end_tag(self):
        packet = copy.deepcopy(self.packet)
        packet['items'][0]['input']['description'] = '</script><img src=x onerror=alert(1)>'
        html = tool.render_review(packet)
        self.assertNotIn('</script><img', html)
        self.assertIn('\\u003c/script>', html)

    def test_empty_collection_is_not_real_data(self):
        value = collection()
        value['cases'] = []
        with self.assertRaisesRegex(ValueError, 'empty intake'):
            tool.validate_collection(value, tool.development_reference(), tool.baseline)

    def test_origin_and_split_strata_not_pooled(self):
        first = self.packet['items'][0]
        annotate(first)
        second = copy.deepcopy(first)
        second['id'] = second['input']['request_id'] = 'unit-2'
        second.update(split='holdout', scenarioFamily='unit-new-family')
        second['origin'].update(kind='real_requirement', authorKind='human')
        second['input']['description'] = 'Unit-test-only distinct media consumption task'
        self.packet['items'].append(second)
        manifest = tool.read_json(self.folder / 'manifest.json')
        report, _ = self.evaluate_refrozen_unit_packet()
        self.assertEqual(set(report['strata']), {'synthetic/development', 'real_requirement/holdout'})
        self.assertNotIn('overall', report)
        self.assertFalse(report['stageDAccepted'])

    def test_archived_cli_audits_offline_and_refuses_pending_scores(self):
        entry = self.folder / 'frozen-inputs/scripts/review-cnf-requirements.py'
        audit_path = Path(self.temp.name) / 'audit.json'
        args = [sys.executable, str(entry), 'audit', '--review', str(self.folder / 'review.json'), '--out', str(audit_path)]
        subprocess.run(args, check=True, capture_output=True, text=True)
        self.assertFalse(tool.read_json(audit_path)['evaluationReady'])
        args[2] = 'evaluate'
        args[-1] = str(Path(self.temp.name) / 'must-not-create')
        process = subprocess.run(args, capture_output=True, text=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(Path(args[-1]).exists())

    def test_archived_cli_evaluation_replays_with_identical_predictions(self):
        annotate(self.packet['items'][0])
        target_labels(self.packet['items'][0])
        self.save()
        entry = self.folder / 'frozen-inputs/scripts/review-cnf-requirements.py'
        result = Path(self.temp.name) / 'evaluation'
        args = [sys.executable, str(entry), 'evaluate', '--review', str(self.folder / 'review.json'), '--out', str(result)]
        subprocess.run(args, check=True, capture_output=True, text=True)
        replay = Path(self.temp.name) / 'replay'
        args[1] = str(result / 'frozen-inputs/scripts/review-cnf-requirements.py')
        args[4] = str(result / 'review.json')
        args[-1] = str(replay)
        subprocess.run(args, check=True, capture_output=True, text=True)
        self.assertEqual(tool.read_json(result / 'report.json'), tool.read_json(replay / 'report.json'))
        self.assertEqual(tool.read_json(result / 'predictions.json'), tool.read_json(replay / 'predictions.json'))

    def test_target_metrics_separate_text_and_structured_input(self):
        item = self.packet['items'][0]
        item['input'].update(description='视频播放，最低 4 Mbps', requested={'max_bandwidth_mbps': 8})
        annotate(item)
        target_labels(item)
        for field, value, evidence in [
                ('min_bandwidth_mbps', 4, {'path': '/description', 'quote': '最低 4 Mbps'}),
                ('max_bandwidth_mbps', 8, {'path': '/requested/max_bandwidth_mbps', 'value': 8})]:
            item['targetReview']['fields'][field].update(value=value, resolution='explicit', evidence=[evidence])
        tool.validate_labels(item, tool.baseline)
        manifest = tool.read_json(self.folder / 'manifest.json')
        report, _ = self.evaluate_refrozen_unit_packet()
        by_source = report['strata']['synthetic/development']['explicitTargetsByEvidence']
        self.assertEqual(by_source['text_only'], {'fields': 1, 'valueMatches': 1})
        self.assertEqual(by_source['structured_only'], {'fields': 1, 'valueMatches': 1})

    def test_agent_synthetic_mode_never_counts_as_human_review(self):
        folder = Path(self.temp.name) / 'agent-mode'
        packet = tool.prepare(collection(), folder, 'agent_synthetic')
        item = packet['items'][0]
        annotate(item)
        item['review'].update(reviewerKind='agent', independentOfPredictions=False)
        target_labels(item)
        (folder / 'review.json').write_text(json.dumps(packet), encoding='utf-8')
        packet, manifest, normalizer = tool.load_bundle(folder / 'review.json')
        report, _ = tool.evaluate(packet, manifest, normalizer)
        self.assertEqual(report['audit']['humanCategoryDeclarations'], 0)
        self.assertEqual(report['audit']['agentCategoryAnnotations'], 1)
        self.assertIn('not an independent benchmark', report['claimScope'])
        self.assertFalse((folder / 'review.html').exists())

    def test_agent_mode_refuses_real_requirements(self):
        value = collection()
        value['cases'][0]['origin'].update(kind='real_requirement', authorKind='human')
        with self.assertRaisesRegex(ValueError, 'restricted'):
            tool.prepare(value, Path(self.temp.name) / 'invalid-agent', 'agent_synthetic')

    def test_agent_mode_cannot_claim_human_or_independent_labels(self):
        item = copy.deepcopy(self.packet['items'][0])
        annotate(item)
        with self.assertRaisesRegex(ValueError, 'non-independent'):
            tool.validate_labels(item, tool.baseline, 'agent_synthetic')
        item['review'].update(reviewerKind='agent', independentOfPredictions=True)
        with self.assertRaises(ValueError):
            tool.validate_labels(item, tool.baseline, 'agent_synthetic')

    def test_evaluate_rechecks_frozen_raw_membership(self):
        annotate(self.packet['items'][0])
        manifest = tool.read_json(self.folder / 'manifest.json')
        self.packet['items'][0]['input']['description'] = 'mutated after freeze'
        with self.assertRaisesRegex(ValueError, 'Raw facts changed'):
            tool.evaluate(self.packet, manifest, tool.baseline)


if __name__ == '__main__':
    unittest.main()
