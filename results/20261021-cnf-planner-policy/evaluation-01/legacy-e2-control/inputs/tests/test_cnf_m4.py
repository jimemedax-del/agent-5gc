"""Placement evidence must identify the actual Ready Pod and preserve the single variable."""
import copy
import importlib.util
from pathlib import Path
import unittest

module = importlib.util.spec_from_file_location('cnf_m4', Path(__file__).resolve().parents[1] / 'scripts/cnf-m4.py')
m4 = importlib.util.module_from_spec(module)
module.loader.exec_module(m4)


class PlacementEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.pod = {'ip': '10.42.2.8', 'uid': 'current-uid'}
        ref = {'name': 'edge-pod', 'uid': 'current-uid'}
        self.endpoint = {'subsets': [{'addresses': [{'ip': self.pod['ip'], 'nodeName': 'ran-test', 'targetRef': ref}],
                                     'ports': [{'port': 5201, 'protocol': 'TCP'}]}]}
        self.slices = [{'ports': [{'port': 5201, 'protocol': 'TCP'}], 'endpoints': [
            {'addresses': [self.pod['ip']], 'nodeName': 'ran-test', 'conditions': {'ready': True}, 'targetRef': ref}]}]

    def validate(self):
        m4.validate_endpoint(self.endpoint, self.slices, 'edge-pod', self.pod, 'ran-test')

    def test_current_ready_pod_on_target_node(self):
        self.validate()

    def test_service_ip_alone_does_not_prove_placement(self):
        self.endpoint['subsets'][0]['addresses'][0]['nodeName'] = 'old-node'
        with self.assertRaisesRegex(RuntimeError, 'expected backend'):
            self.validate()

    def test_old_pod_at_same_ip_is_rejected(self):
        self.endpoint['subsets'][0]['addresses'][0]['targetRef']['uid'] = 'old-uid'
        with self.assertRaisesRegex(RuntimeError, 'expected backend'):
            self.validate()

    def test_stale_slice_is_rejected(self):
        self.slices[0]['endpoints'][0]['addresses'] = ['10.42.0.8']
        with self.assertRaisesRegex(RuntimeError, 'EndpointSlice'):
            self.validate()

    def test_unready_slice_is_rejected(self):
        self.slices[0]['endpoints'][0]['conditions']['ready'] = False
        with self.assertRaisesRegex(RuntimeError, 'EndpointSlice'):
            self.validate()

    def test_duplicate_endpoints_are_rejected(self):
        self.slices[0]['endpoints'].append(copy.deepcopy(self.slices[0]['endpoints'][0]))
        with self.assertRaisesRegex(RuntimeError, 'EndpointSlice'):
            self.validate()

    def test_non_ready_legacy_address_is_rejected(self):
        self.endpoint['subsets'][0]['notReadyAddresses'] = [{'ip': '10.42.2.9'}]
        with self.assertRaisesRegex(RuntimeError, 'one Ready'):
            self.validate()

    def test_only_edge_node_selector_changes_after_normalization(self):
        neutral = m4.render(False)[3]
        placed = m4.render(True)[3]
        self.assertEqual(neutral[0], placed[0])
        neutral[1]['spec']['template']['spec']['nodeSelector'] = m4.render(True)[0]['edgeNodeSelector']
        self.assertEqual(neutral, placed)

    def test_leftover_tbf_cannot_count_as_neutral(self):
        with self.assertRaisesRegex(RuntimeError, 'unshaped'):
            m4.no_shaping([{'kind': 'tbf', 'root': True}])

    def test_unrelated_queue_does_not_hide_tbf(self):
        with self.assertRaisesRegex(RuntimeError, 'unshaped'):
            m4.no_shaping([{'kind': 'noqueue', 'root': True}, {'kind': 'tbf'}])


if __name__ == '__main__':
    unittest.main()
