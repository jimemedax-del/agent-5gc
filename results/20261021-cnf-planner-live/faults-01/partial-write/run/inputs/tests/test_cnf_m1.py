"""Do not accept a completed iperf run as proof of an intact service chain."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('cnf_m1', Path(__file__).resolve().parents[1] / 'scripts/cnf-m1.py')
m1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m1)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.iperf = {'end': {'sum_received': {'bytes': 3000000, 'bits_per_second': 4800000}}}
        version = json.loads(m1.LOCK.read_text(encoding='utf-8'))['configVersion']
        self.connections = [dict(experiment=version, cluster='m1_backend',
                                 upstream='10.43.1.2:5201', flags='-', bytes_received=str(n))
                            for n in [100, 3000000]]
        self.backend = 'Accepted connection from 10.42.0.180, port 34567\n'
        self.before = {'interfaces': 'upfgtp: 100 1 0 0 0 0 0 0 100 1 0 0 0 0 0 0'}
        self.after = {'interfaces': 'upfgtp: 3100000 2000 0 0 0 0 0 0 100000 1000 0 0 0 0 0 0'}

    def check(self):
        return m1.validate_evidence(self.iperf, '\n'.join(json.dumps(x) for x in self.connections),
                                    self.backend, '10.42.0.180', '10.43.1.2', self.before, self.after)

    def test_complete_chain_evidence(self):
        result = self.check()
        self.assertEqual(result['envoySuccessfulConnections'], 2)
        self.assertEqual(result['upfGtpRxBytesDelta'], 3099900)

    def test_successful_direct_iperf_is_insufficient(self):
        self.connections = []
        with self.assertRaisesRegex(RuntimeError, 'Envoy'):
            self.check()

    def test_data_log_alone_is_pending_not_accepted(self):
        self.connections = self.connections[1:]
        with self.assertRaises(m1.PendingProxyLogs):
            self.check()

    def test_wrong_upstream_is_not_a_success(self):
        for connection in self.connections:
            connection['upstream'] = '10.43.1.3:5201'
        with self.assertRaisesRegex(RuntimeError, 'Envoy'):
            self.check()

    def test_backend_must_observe_proxy_peer(self):
        self.backend = 'Accepted connection from 192.168.244.129, port 34567\n'
        with self.assertRaisesRegex(RuntimeError, 'Backend'):
            self.check()

    def test_bypassing_upf_is_not_a_success(self):
        self.after = self.before
        with self.assertRaisesRegex(RuntimeError, 'UPF'):
            self.check()

    def test_pod_replacement_cannot_hide_zero_restart_count(self):
        first = {'free5gc': {'upf': {'uid': 'old', 'phase': 'Running', 'containers': [
            {'name': 'upf', 'containerID': 'first', 'restartCount': 0, 'ready': True,
             'state': {'running': {}}}]}}}
        last = copy.deepcopy(first)
        last['free5gc']['upf']['uid'] = 'new'
        with self.assertRaisesRegex(RuntimeError, 'identity'):
            m1.assert_steady(first, last)

    def test_runtime_display_name_is_not_an_image_identity(self):
        expected = 'docker.io/example/envoy@sha256:manifest'
        pod = {'containers': [{'specImage': expected, 'image': 'sha256:config',
                               'imageID': expected}]}
        m1.verify_image_lock(pod, expected)

    def test_actual_digest_mismatch_is_rejected(self):
        expected = 'docker.io/example/envoy@sha256:manifest'
        pod = {'containers': [{'specImage': expected, 'image': expected,
                               'imageID': 'example/envoy@sha256:other'}]}
        with self.assertRaisesRegex(RuntimeError, 'imageID Digest'):
            m1.verify_image_lock(pod, expected)

    def test_matching_runtime_digest_does_not_allow_mutable_spec(self):
        expected = 'docker.io/example/envoy@sha256:manifest'
        pod = {'containers': [{'specImage': 'docker.io/example/envoy:latest',
                               'imageID': expected}]}
        with self.assertRaisesRegex(RuntimeError, 'image spec'):
            m1.verify_image_lock(pod, expected)


if __name__ == '__main__':
    unittest.main()
