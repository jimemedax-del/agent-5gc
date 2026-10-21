"""Fixed-state adoption and snapshot validation must reject drift before mutation."""
import copy
import importlib.util
from pathlib import Path
import unittest

module = importlib.util.spec_from_file_location('cnf_profile', Path(__file__).resolve().parents[1] / 'scripts/cnf-profile.py')
p = importlib.util.module_from_spec(module)
module.loader.exec_module(p)


class FixedProfileTests(unittest.TestCase):
    def objects(self, state):
        return {(d['kind'], d['metadata']['name']): copy.deepcopy(d) for d in p.render(state)}

    def test_four_states_have_unique_recognition(self):
        for state in p.profile()['states']:
            self.assertEqual(p.detect(self.objects(state)), state)

    def test_additional_allow_policy_is_not_adopted(self):
        objects = self.objects('protected')
        objects['NetworkPolicy', 'allow-all'] = {'kind': 'NetworkPolicy'}
        with self.assertRaisesRegex(RuntimeError, 'refuse to overwrite'):
            p.detect(objects)

    def test_modified_node_selector_is_not_adopted(self):
        objects = self.objects('protected')
        objects['Deployment', 'cnf-m2-edge']['spec']['template']['spec']['nodeSelector'] = {'kubernetes.io/hostname': 'other'}
        with self.assertRaises(RuntimeError):
            p.detect(objects)

    def test_unexpected_environment_is_not_adopted(self):
        objects = self.objects('protected')
        objects['Deployment', 'cnf-m1-backend']['spec']['template']['spec']['containers'][0]['env'] = [{'name': 'EXTRA', 'value': 'x'}]
        with self.assertRaises(RuntimeError):
            p.detect(objects)

    def test_additional_capability_is_not_adopted(self):
        objects = self.objects('protected')
        objects['Deployment', 'cnf-m1-backend']['spec']['template']['spec']['containers'][0]['securityContext']['capabilities']['add'] = ['NET_ADMIN']
        with self.assertRaises(RuntimeError):
            p.detect(objects)

    def test_valid_fixed_snapshot(self):
        self.assertEqual(p.validate_snapshot(p.snapshot('protected', {})), 'protected')

    def test_snapshot_state_edit_without_matching_manifest_is_rejected(self):
        value = p.snapshot('protected', {})
        value['state'] = 'qos'
        with self.assertRaises(ValueError):
            p.validate_snapshot(value)

    def test_arbitrary_state_is_rejected(self):
        with self.assertRaises(ValueError):
            p.validate_snapshot({'profile': p.profile()['configVersion'], 'state': 'arbitrary-yaml'})


if __name__ == '__main__':
    unittest.main()
