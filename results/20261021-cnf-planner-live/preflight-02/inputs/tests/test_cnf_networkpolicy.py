"""Prevent permissive peers and distinguish genuine connection denial from tool failure."""
import importlib.util
from pathlib import Path
import unittest

module = importlib.util.spec_from_file_location('netpol', Path(__file__).resolve().parents[1] / 'scripts/cnf-networkpolicy.py')
netpol = importlib.util.module_from_spec(module)
module.loader.exec_module(netpol)


class PolicyEvidenceTests(unittest.TestCase):
    def test_tcp_success_is_not_denial(self):
        with self.assertRaises(RuntimeError):
            netpol.validate_connection(0, '', False)

    def test_missing_command_is_not_denial(self):
        with self.assertRaises(RuntimeError):
            netpol.validate_connection(127, 'timeout: command not found', False)

    def test_kubectl_error_is_not_denial(self):
        with self.assertRaises(RuntimeError):
            netpol.validate_connection(1, 'error: unable to upgrade connection', False)

    def test_timeout_is_denial(self):
        netpol.validate_connection(124, '', False)

    def test_explicit_network_rejection_is_denial(self):
        netpol.validate_connection(1, 'bash: connect: Connection refused', False)

    def test_failed_connection_is_not_allowed(self):
        with self.assertRaises(RuntimeError):
            netpol.validate_connection(124, '', True)

    def test_backend_ingress_peer_is_same_namespace_envoy_only(self):
        policies = netpol.render()[5]
        backend = policies[0]['spec']
        self.assertEqual(backend['policyTypes'], ['Ingress'])
        self.assertEqual(backend['ingress'], [{'from': [{'podSelector': {'matchLabels': {'app': 'cnf-m1-envoy'}}}],
                                             'ports': [{'protocol': 'TCP', 'port': 5201}]}])

    def test_dns_namespace_and_pod_selectors_share_one_peer(self):
        dns = netpol.render()[5][1]['spec']['egress'][1]
        self.assertEqual(len(dns['to']), 1)
        self.assertEqual(set(dns['to'][0]), {'namespaceSelector', 'podSelector'})
        self.assertEqual({p['protocol'] for p in dns['ports']}, {'TCP', 'UDP'})

    def test_diagnostic_pods_cannot_match_allowed_labels(self):
        for pod in netpol.render()[6]:
            self.assertNotIn(pod['metadata']['labels']['app'], ['cnf-m1-envoy', 'cnf-m1-backend', 'cnf-m2-edge'])
            self.assertFalse(pod['spec']['automountServiceAccountToken'])
            self.assertEqual(pod['spec']['containers'][0]['securityContext']['capabilities'], {'drop': ['ALL']})


if __name__ == '__main__':
    unittest.main()
