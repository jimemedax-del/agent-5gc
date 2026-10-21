"""Guard downlink direction, active shaping evidence and the agreed batch thresholds."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

module = importlib.util.spec_from_file_location('cnf_m3', Path(__file__).resolve().parents[1] / 'scripts/cnf-m3.py')
m3 = importlib.util.module_from_spec(module)
module.loader.exec_module(m3)


class DownlinkTests(unittest.TestCase):
    def setUp(self):
        self.spec, self.routing, _, _ = m3.render()
        self.route = self.routing['classes'][0]
        self.data = {'start': {'cookie': 'test-cookie', 'test_start': {'reverse': 1}},
            'end': {'sum_received': {'bytes': 9000000, 'seconds': 15, 'bits_per_second': 4800000},
                    'sum_sent': {'bytes': 9500000, 'seconds': 15, 'bits_per_second': 5066666}}}
        self.logs = [dict(experiment=self.routing['configVersion'], start='2026-10-15T14:00:01Z',
            business_class='core', listener='m2_core', cluster='m2_core', upstream='10.43.1.2:5201',
            local='10.42.0.1:15201', flags='-', bytes_received=400, bytes_sent=n) for n in [400, 9000000]]

    def validate(self):
        return m3.validate_downlink(self.data, '\n'.join(json.dumps(x) for x in self.logs),
            'Accepted connection from 10.42.0.1, port 3333\nCookie: test-cookie\n', '',
            self.route, self.routing, '10.43.1.2', '10.42.0.1',
            '2026-10-15T14:00:00Z', '2026-10-15T14:00:20Z', 15)

    def test_receiver_is_authoritative_and_raw_sender_is_retained(self):
        before = copy.deepcopy(self.data)
        result = self.validate()
        self.assertEqual(result['receiverMbps'], 4.8)
        self.assertEqual(result['senderBytes'], 9500000)
        self.assertEqual(self.data, before)

    def test_uplink_bytes_cannot_prove_downlink(self):
        self.logs[1].update(bytes_received=9000000, bytes_sent=400)
        with self.assertRaisesRegex(RuntimeError, 'missing payload'):
            self.validate()

    def test_uplink_mode_is_rejected(self):
        self.data['start']['test_start']['reverse'] = 0
        with self.assertRaisesRegex(RuntimeError, 'downlink receiver'):
            self.validate()

    def test_partial_duration_is_rejected(self):
        self.data['end']['sum_received']['seconds'] = 2
        with self.assertRaisesRegex(RuntimeError, 'downlink receiver'):
            self.validate()


class ShapingTests(unittest.TestCase):
    def setUp(self):
        self.before = [dict(kind='tbf', root=True, handle='1:', options={'rate': 625000},
                            bytes=1000, packets=10, drops=0, overlimits=0, requeues=0, backlog=0, qlen=0)]
        self.after = copy.deepcopy(self.before)
        self.after[0].update(bytes=10000000, packets=7000, overlimits=2000)
        self.spec = m3.render()[0]
        self.rows = [dict(businessClass=name, outcome='passed', receiverMbps=rate,
                         proxyConnections=2) for _ in range(5) for name, rate in [('core', 4.8), ('edge', 14.4)]]

    def test_active_counter_growth(self):
        self.assertEqual(m3.qdisc_delta(self.before, self.after, 5, 9000000)['overlimits'], 2000)

    def test_rate_drift_is_rejected(self):
        self.after[0]['options']['rate'] = 1875000
        with self.assertRaisesRegex(RuntimeError, 'rate differs'):
            m3.qdisc_delta(self.before, self.after, 5, 9000000)

    def test_qdisc_without_shaping_activity_is_rejected(self):
        self.after[0]['overlimits'] = 0
        with self.assertRaisesRegex(RuntimeError, 'active TBF'):
            m3.qdisc_delta(self.before, self.after, 5, 9000000)

    def test_unrelated_small_counter_growth_is_rejected(self):
        self.after[0]['bytes'] = 2000
        with self.assertRaisesRegex(RuntimeError, 'transmitted payload'):
            m3.qdisc_delta(self.before, self.after, 5, 9000000)

    def test_full_batch_thresholds(self):
        result = m3.summarize(self.rows, self.spec, 5)
        self.assertTrue(result['fullAcceptance'])
        self.assertEqual(result['medianRatio'], 3)

    def test_missing_round_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
            m3.summarize(self.rows[:-1], self.spec, 5)

    def test_median_cap_excess_is_rejected(self):
        for row in self.rows:
            if row['businessClass'] == 'core':
                row['receiverMbps'] = 5.751
        with self.assertRaisesRegex(RuntimeError, '15%'):
            m3.summarize(self.rows, self.spec, 5)

    def test_insufficient_ratio_is_rejected(self):
        for row in self.rows:
            if row['businessClass'] == 'edge':
                row['receiverMbps'] = 9.5
        with self.assertRaisesRegex(RuntimeError, 'ratio'):
            m3.summarize(self.rows, self.spec, 5)

    def test_smoke_does_not_claim_full_acceptance(self):
        self.assertFalse(m3.summarize(self.rows[:2], self.spec, 1)['fullAcceptance'])


if __name__ == '__main__':
    unittest.main()
