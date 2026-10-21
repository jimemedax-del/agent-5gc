"""Reject completed traffic unless both routing and independent backend evidence agree."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

specification = importlib.util.spec_from_file_location('cnf_m2', Path(__file__).resolve().parents[1] / 'scripts/cnf-m2.py')
m2 = importlib.util.module_from_spec(specification)
specification.loader.exec_module(m2)


class RoutingEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.spec, _, _ = m2.render()
        self.route = self.spec['classes'][0]
        payload = self.spec['minimumPayloadBytes'] * 8
        self.data = {'start': {'cookie': 'unique-session-cookie'}, 'end': {
            'sum_sent': {'bytes': payload}, 'sum_received': {'bytes': payload}}}
        self.records = [dict(experiment=self.spec['configVersion'], start='2026-10-15T14:00:01Z',
                             business_class='core', listener='m2_core', cluster='m2_core',
                             upstream='10.43.1.2:5201', local='10.42.0.1:15201', flags='-', bytes_received=n)
                        for n in [400, payload + 37]]
        self.expected = 'Accepted connection from 10.42.0.1, port 3333\nCookie: unique-session-cookie\n'
        self.other = ''

    def validate(self):
        return m2.validate_round(self.data, '\n'.join(json.dumps(x) for x in self.records), self.expected,
                                 self.other, self.route, self.spec, '10.43.1.2', '10.42.0.1',
                                 '2026-10-15T14:00:00Z', '2026-10-15T14:00:12Z')

    def test_complete_routing_evidence(self):
        self.assertEqual(self.validate()['backendHit'], 'core')

    def test_cookie_at_wrong_backend_is_a_failure(self):
        self.other = self.expected
        with self.assertRaisesRegex(RuntimeError, 'other backend'):
            self.validate()

    def test_wrong_cluster_cannot_be_hidden_by_successful_iperf(self):
        self.records[1]['cluster'] = 'm2_edge'
        with self.assertRaisesRegex(RuntimeError, 'Wrong class'):
            self.validate()

    def test_previous_session_logs_do_not_count(self):
        for item in self.records:
            item['start'] = '2026-10-15T13:59:59Z'
        with self.assertRaises(m2.m1.PendingProxyLogs):
            self.validate()

    def test_completed_but_incomplete_payload_is_rejected(self):
        self.data['end']['sum_received']['bytes'] = 100
        with self.assertRaisesRegex(RuntimeError, 'matching nonempty per-session payload'):
            self.validate()

    def test_matching_zero_counters_do_not_prove_data_transfer(self):
        self.data['end']['sum_sent']['bytes'] = 0
        self.data['end']['sum_received']['bytes'] = 0
        with self.assertRaisesRegex(RuntimeError, 'matching nonempty'):
            self.validate()

    def test_one_proxy_connection_is_not_a_complete_session(self):
        self.records.pop()
        with self.assertRaises(m2.m1.PendingProxyLogs):
            self.validate()

    def test_backend_cookie_is_required_independently(self):
        self.expected = self.expected.replace('unique-session-cookie', 'old-cookie')
        with self.assertRaisesRegex(RuntimeError, 'cookie/proxy peer'):
            self.validate()


if __name__ == '__main__':
    unittest.main()
