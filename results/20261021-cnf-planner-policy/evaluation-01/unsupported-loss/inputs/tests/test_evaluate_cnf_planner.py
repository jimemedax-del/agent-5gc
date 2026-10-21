import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('evaluate_planner', ROOT / 'scripts/evaluate-cnf-planner.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class PlannerEvaluationTests(unittest.TestCase):
    def test_fixture_expectations_do_not_reach_planner(self):
        fixtures = tool.planner.read_json(tool.FIXTURES)
        fixtures['cases'] = fixtures['cases'][:1]
        first_report, first = tool.evaluate(fixtures)
        other = copy.deepcopy(fixtures)
        other['cases'][0]['expected']['mode'] = 'wrong-annotation'
        second_report, second = tool.evaluate(other)
        self.assertEqual(first[0]['planResult'], second[0]['planResult'])
        self.assertNotEqual(first_report['expectationMatches'], second_report['expectationMatches'])

    def test_engineering_fixtures_preserve_negative_cases_and_claim_scope(self):
        report, records = tool.evaluate(tool.planner.read_json(tool.FIXTURES))
        self.assertEqual(report['expectationMatches'], report['cases'])
        self.assertGreater(report['notPlanned'], 0)
        self.assertGreater(report['planned'], 0)
        self.assertEqual(report['executionAuthorizations'], 0)
        self.assertEqual(len(records), report['cases'])
        self.assertFalse(report['EStageFullyAccepted'])

    def test_duplicate_fixtures_rejected(self):
        fixtures = tool.planner.read_json(tool.FIXTURES)
        fixtures['cases'].append(copy.deepcopy(fixtures['cases'][0]))
        with self.assertRaises(tool.planner.Rejection):
            tool.evaluate(fixtures)


if __name__ == '__main__':
    unittest.main()
