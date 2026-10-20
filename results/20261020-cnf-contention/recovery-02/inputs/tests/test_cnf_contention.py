import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / file)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


tool = load('contention', 'cnf-contention.py')
audit = load('audit_contention', 'audit-cnf-contention.py')


class ContentionSafetyTests(unittest.TestCase):
    def test_only_new_namespace_and_one_shared_network_namespace(self):
        docs = tool.render()
        self.assertEqual([d['kind'] for d in docs], ['Namespace', 'Deployment', 'Service', 'NetworkPolicy'])
        self.assertTrue(all(d['metadata'].get('namespace', tool.NS) == tool.NS for d in docs))
        pod = docs[1]['spec']['template']['spec']
        self.assertEqual(len(pod['containers']), 3)
        self.assertFalse(pod.get('hostNetwork', False))
        self.assertFalse(pod['automountServiceAccountToken'])
        self.assertFalse(any('hostPath' in v for v in pod['volumes']))

    def test_only_queue_container_has_net_admin(self):
        containers = tool.render()[1]['spec']['template']['spec']['containers']
        for c in containers:
            self.assertFalse(c['securityContext']['allowPrivilegeEscalation'])
            self.assertTrue(c['securityContext']['readOnlyRootFilesystem'])
            self.assertEqual(c['securityContext']['capabilities'],
                             {'drop': ['ALL'], 'add': ['NET_ADMIN']} if c['name'] == 'queue' else {'drop': ['ALL']})

    def test_guaranteed_capacity_includes_margin_and_rejects_excess(self):
        with self.assertRaisesRegex(RuntimeError, 'capacity'):
            tool.admit({'ue1': {'rate': 6, 'ceil': 8, 'prio': 0}, 'ue2': {'rate': 5, 'ceil': 8, 'prio': 1}})
        self.assertEqual(sum(v['rate'] for v in tool.policy('guarantee-ue1').values()), 8)

    def test_nonfinite_and_boolean_policy_parameters_rejected(self):
        for value in [float('nan'), float('inf'), True, -1]:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                tool.admit({'ue1': {'rate': value, 'ceil': 8, 'prio': 0}, 'ue2': {'rate': 2, 'ceil': 4, 'prio': 1}})

    def test_unknown_user_and_extra_fields_are_rejected(self):
        with self.assertRaises(RuntimeError):
            tool.admit({'stranger': {'rate': 2, 'ceil': 8, 'prio': 0}})
        value = tool.policy('equal')
        value['ue1']['command'] = 'arbitrary'
        with self.assertRaises(RuntimeError):
            tool.admit(value)

    def test_priority_comparison_only_changes_priority(self):
        first, second = tool.policy('priority-ue1'), tool.policy('priority-ue2')
        for u in first:
            self.assertEqual(first[u]['rate'], second[u]['rate'])
            self.assertEqual(first[u]['ceil'], second[u]['ceil'])
            self.assertNotEqual(first[u]['prio'], second[u]['prio'])

    def test_no_shaping_commands_touch_host_or_other_interfaces(self):
        for mode in tool.config()['modes']:
            for cmd in tool.tc_commands(mode):
                self.assertIn('dev eth0', cmd)
                self.assertNotIn('upfgtp', cmd)
                self.assertNotIn('netns', cmd)
        self.assertEqual(tool.tc_commands('none'), [])

    def test_user_probe_filters_are_distinct(self):
        rows = tool.tc_commands('equal')
        self.assertTrue(any('protocol 1 0xff match ip tos 40 0xfc flowid 1:10' in x for x in rows))
        self.assertTrue(any('protocol 1 0xff match ip tos 80 0xfc flowid 1:20' in x for x in rows))

    def test_empty_tc_class_and_filter_streams_are_legitimate(self):
        with patch.object(tool, 'queue', side_effect=['[{"kind":"noqueue","root":true}]', '', '']):
            self.assertEqual(tool.tc_state()['class'], [])
        with patch.object(tool, 'queue', return_value=''):
            with self.assertRaisesRegex(RuntimeError, 'Missing qdisc'):
                tool.tc_state()


class ContentionAuditTests(unittest.TestCase):
    def test_native_htb_and_u32_decoding_and_truncation_rejection(self):
        cls = 'class htb 1:10 parent 1:1 leaf 10: prio 0 rate 4Mbit ceil 8Mbit burst 32Kb cburst 32Kb\n Sent 1234 bytes 9 pkt (dropped 0, overlimits 0 requeues 0)\n'
        filt = 'filter parent 1: protocol ip pref 10 u32 chain 0 fh 800::800 flowid 1:10\n match 00110000/00ff0000 at 8\n match 14510000/ffff0000 at 20\n'
        result = audit.parse_tc_native([], cls, filt)
        self.assertEqual(result['class'][0]['options']['rate'], 500000)
        self.assertEqual(result['class'][0]['packets'], 9)
        self.assertEqual(result['filter'][0]['options']['matches'][1], ['14510000', 'ffff0000', 20])
        self.assertEqual(len(audit.parse_tc_native([], cls + 'class fq_codel 10:300 parent 10:\n (dropped 0)\n', filt)['class']), 1)
        with self.assertRaises(ValueError):
            audit.parse_tc_native([], cls.split('Sent')[0], filt)

    def row(self, mode, first, second):
        return {'folder': mode, 'mode': mode, 'aggregateSteadyMbps': first + second,
                'users': {u: {'steadyMbps': v, 'steadyLostPackets': 100, 'windowMbps': [v] * 19}
                          for u, v in [('ue1', first), ('ue2', second)]}}

    def test_missing_guarantee_is_failure_even_with_high_utilization(self):
        rows = [self.row('guarantee-ue1', 4.8, 4.8)]
        result = audit.performance(rows, tool.config())
        self.assertFalse(result['passed'])
        self.assertFalse(result['checks'][0]['checks']['HighRateBounds'])

    def test_role_reversal_is_checked(self):
        self.assertTrue(audit.performance([self.row('priority-ue1', 7.6, 1.9)], tool.config())['passed'])
        self.assertFalse(audit.performance([self.row('priority-ue2', 7.6, 1.9)], tool.config())['passed'])

    def test_window_violations_cannot_be_hidden_in_mean(self):
        row = self.row('guarantee-ue1', 7.6, 1.9)
        row['users']['ue1']['windowMbps'] = [0, 0, 0] + [8] * 16
        self.assertFalse(audit.performance([row], tool.config())['passed'])

    def test_unshaped_control_cannot_be_shaped(self):
        with self.assertRaises(ValueError):
            audit.check_tc({'qdisc': [{'root': True, 'kind': 'htb', 'handle': '1:'}], 'class': [], 'filter': []}, 'none')

    def test_percentiles_are_nearest_rank(self):
        self.assertEqual(audit.percentile([1, 2, 3, 4], .95), 4)
        with self.assertRaises(ValueError):
            audit.percentile([], .5)


if __name__ == '__main__':
    unittest.main()
