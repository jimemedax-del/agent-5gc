import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('evaluation', ROOT / 'scripts/evaluate-cnf-requirements.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class EvaluationTests(unittest.TestCase):
    def dataset(self):
        value = tool.baseline.read_json(ROOT / 'tests/fixtures/requirements-cases.json')
        value['cases'] = value['cases'][:1]
        return value

    def test_changing_annotation_does_not_change_prediction(self):
        first = self.dataset()
        second = copy.deepcopy(first)
        second['cases'][0]['expected']['category'] = 'bulk'
        report1, records1, _ = tool.evaluate_cases(first)
        report2, records2, _ = tool.evaluate_cases(second)
        self.assertEqual(records1[0]['prediction'], records2[0]['prediction'])
        self.assertNotEqual(report1['overall']['draftCategoryAgreements'], report2['overall']['draftCategoryAgreements'])

    def test_disagreements_retained_and_not_a_network_claim(self):
        value = self.dataset()
        value['cases'][0]['expected']['category'] = 'bulk'
        report, _, _ = tool.evaluate_cases(value)
        self.assertTrue(report['disagreementCaseIds'])
        self.assertFalse(report['independentHumanValidation'])
        self.assertFalse(report['clusterAccessed'])

    def test_families_must_not_cross_development_groups(self):
        value = self.dataset()
        case = copy.deepcopy(value['cases'][0])
        case['id'] = case['input']['request_id'] = 'second'
        case['split'] = 'challenge_synthetic'
        value['cases'].append(case)
        with self.assertRaises(ValueError):
            tool.evaluate_cases(value)

    def test_duplicate_case_ids_rejected(self):
        value = self.dataset()
        value['cases'].append(copy.deepcopy(value['cases'][0]))
        with self.assertRaises(ValueError):
            tool.evaluate_cases(value)


if __name__ == '__main__':
    unittest.main()
