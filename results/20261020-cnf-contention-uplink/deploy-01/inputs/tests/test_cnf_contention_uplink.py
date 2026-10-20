import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('c2', ROOT / 'scripts/cnf-contention-uplink.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
audit = tool.audit


def data():
    start = {'test_start': {'protocol': 'UDP', 'reverse': 0, 'duration': 30, 'blksize': 1200}, 'cookie': 'unique'}
    received = [{'sum': {'start': n, 'end': n + 1, 'seconds': 1, 'sender': False,
                         'bytes': 500400, 'packets': 833, 'lost_packets': 416}} for n in range(30)]
    sent = [{'sum': {'start': n, 'end': n + 1, 'seconds': 1, 'sender': True,
                     'bytes': 999600, 'packets': 833}} for n in range(30)]
    return {'start': copy.deepcopy(start), 'intervals': sent,
            'server_output_json': {'start': copy.deepcopy(start), 'intervals': received}}


def network():
    return {'eth0Qdisc': [{'root': True, 'kind': 'noqueue', 'handle': '0:'}, {'kind': 'ingress', 'handle': 'ffff:'}],
            'link': [{'ifname': tool.DEVICE, 'ifalias': tool.VERSION, 'mtu': 1450,
                      'linkinfo': {'info_kind': 'ifb'}, 'flags': ['UP']}],
            'ingressFilters': [{'protocol': 'ip', 'pref': 45000, 'kind': 'matchall', 'chain': 0},
                              {'protocol': 'ip', 'pref': 45000, 'kind': 'matchall', 'chain': 0,
                               'options': {'handle': 1, 'actions': [{'kind': 'mirred', 'mirred_action': 'redirect',
                                   'direction': 'egress', 'to_dev': tool.DEVICE, 'control_action': {'type': 'stolen'}}]}}]}


class UplinkTests(unittest.TestCase):
    def test_separate_namespace_ports_and_json_receiver(self):
        docs = tool.render()
        self.assertEqual(docs[0]['metadata']['name'], tool.NS)
        self.assertEqual({p['nodePort'] for p in docs[2]['spec']['ports']}, {31403, 31404})
        containers = docs[1]['spec']['template']['spec']['containers']
        self.assertTrue(all('-J' in c['args'] for c in containers[:2]))
        self.assertTrue(all(c['securityContext']['runAsNonRoot'] for c in containers[:2]))
        self.assertEqual(containers[-1]['securityContext']['capabilities'], {'drop': ['ALL'], 'add': ['NET_ADMIN']})

    def test_queue_targets_only_owned_ifb_and_destination_ports(self):
        for mode in tool.config()['modes']:
            rows = tool.tc_commands(mode)
            self.assertTrue(all('dev ' + tool.DEVICE in row for row in rows))
            self.assertFalse(any('dev eth0' in row or 'sport' in row for row in rows))
        self.assertTrue(any('dport 5201' in row for row in tool.tc_commands('equal')))

    def test_c1_contract_and_users_retained_except_direction_and_nodeports(self):
        self.assertEqual(tool.config()['acceptance'], tool.d.config()['acceptance'])
        self.assertEqual(tool.config()['direction'], 'uplink')
        self.assertEqual([u['supi'] for u in tool.config()['users']], [u['supi'] for u in tool.d.config()['users']])

    def test_receiver_uses_server_bytes_not_client_sent_bytes(self):
        result = audit.receiver(data(), tool.config())
        self.assertAlmostEqual(result['steadyMbps'], 4.0032)
        self.assertAlmostEqual(result['actualSenderSteadyMbps'], 7.9968)

    def test_client_only_statistics_rejected(self):
        value = data()
        del value['server_output_json']
        with self.assertRaisesRegex(ValueError, 'Missing server'):
            audit.receiver(value, tool.config())

    def test_sender_masquerading_as_receiver_rejected(self):
        value = data()
        value['server_output_json']['intervals'][5]['sum']['sender'] = True
        with self.assertRaises(ValueError):
            audit.receiver(value, tool.config())

    def test_datagram_byte_mismatch_rejected(self):
        value = data()
        value['server_output_json']['intervals'][5]['sum']['bytes'] += 1200
        with self.assertRaises(ValueError):
            audit.receiver(value, tool.config())

    def test_wrong_direction_or_cookie_rejected(self):
        for key, replacement in [('cookie', 'other'), ('test_start', {'protocol': 'UDP', 'reverse': 1, 'duration': 30, 'blksize': 1200})]:
            value = data()
            value['server_output_json']['start'][key] = replacement
            with self.assertRaises(ValueError):
                audit.receiver(value, tool.config())

    def test_actual_offered_rate_must_saturate_bottleneck(self):
        value = data()
        for interval in value['intervals']:
            interval['sum'].update(bytes=499200, packets=416)
        with self.assertRaisesRegex(ValueError, 'Actual offered load'):
            audit.receiver(value, tool.config())

    def test_wrong_redirect_target_and_mirror_rejected(self):
        for key, replacement in [('to_dev', 'foreign'), ('mirred_action', 'mirror')]:
            value = network()
            value['ingressFilters'][1]['options']['actions'][0][key] = replacement
            with self.assertRaises(ValueError):
                audit.check_network(value)

    def test_foreign_ifb_and_egress_shaping_rejected(self):
        for mutation in [lambda v: v['link'][0].update(ifalias='foreign'),
                         lambda v: v['eth0Qdisc'][0].update(kind='htb')]:
            value = network()
            mutation(value)
            with self.assertRaises(ValueError):
                audit.check_network(value)

    def test_restoration_requires_both_ifb_and_ingress_removed(self):
        clean = {'eth0Qdisc': [{'root': True, 'kind': 'noqueue', 'handle': '0:'}], 'link': [], 'ingressFilters': []}
        audit.check_network(clean, clean=True)
        for key in ['link', 'ingressFilters']:
            value = copy.deepcopy(clean)
            value[key] = network()[key]
            with self.assertRaises(ValueError):
                audit.check_network(value, clean=True)


if __name__ == '__main__':
    unittest.main()
