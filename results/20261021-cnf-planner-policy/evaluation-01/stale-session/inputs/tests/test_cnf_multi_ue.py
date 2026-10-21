import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('multi_ue', ROOT / 'scripts/cnf-multi-ue.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class MultiUEContractTests(unittest.TestCase):
    def setUp(self):
        self.spec, self.docs = tool.render()

    def test_fixture_cannot_replace_original_workloads(self):
        for doc in self.docs:
            if doc['kind'] == 'Namespace':
                self.assertEqual(doc['metadata']['name'], 'cnf-multi-ue')
            elif doc['metadata']['namespace'] == 'free5gc':
                self.assertEqual(doc['metadata']['name'], 'cnf-a-ue2')
                self.assertIn(doc['kind'], ['Deployment', 'ConfigMap'])
            else:
                self.assertEqual(doc['metadata']['namespace'], 'cnf-multi-ue')

    def test_ue2_does_not_match_original_helm_selector(self):
        ue = next(d for d in self.docs if d['kind'] == 'Deployment' and d['metadata']['name'] == 'cnf-a-ue2')
        self.assertNotEqual(ue['spec']['template']['metadata']['labels'].get('app'), 'ueransim')
        self.assertEqual(ue['spec']['replicas'], 1)
        self.assertFalse(ue['spec']['template']['spec'].get('hostNetwork', False))

    def test_distinct_runtime_identities_and_authentication(self):
        ue1 = json.loads((ROOT / 'infra/free5gc-test-subscriber-ue1.json').read_text())
        ue2 = json.loads((ROOT / 'infra/free5gc-test-subscriber-ue2.json').read_text())
        self.assertNotEqual(ue1['ueId'], ue2['ueId'])
        self.assertNotEqual(ue1['AuthenticationSubscription']['permanentKey'], ue2['AuthenticationSubscription']['permanentKey'])
        cm = next(d for d in self.docs if d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'cnf-a-ue2')
        import yaml
        runtime = yaml.safe_load(cm['data']['ue-config.yaml'])
        self.assertEqual(runtime['supi'], ue2['ueId'])
        self.assertEqual(runtime['key'], ue2['AuthenticationSubscription']['permanentKey']['permanentKeyValue'])
        self.assertEqual(runtime['op'], ue2['AuthenticationSubscription']['opc']['opcValue'])

    def test_each_concurrent_user_has_dedicated_server_and_route(self):
        backends = [d for d in self.docs if d['kind'] == 'Deployment' and
                    d['spec']['template']['metadata']['labels'].get('role') == 'backend']
        self.assertEqual(len(backends), 4)
        self.assertEqual(len({d['spec']['selector']['matchLabels']['app'] for d in backends}), 4)
        self.assertEqual(len({r['nodePort'] for r in self.spec['routes']}), 4)
        for d in backends:
            node = d['spec']['template']['spec']['nodeSelector']
            self.assertEqual(node, {'5gc.free5gc.org/plane': 'control'} if '-core-' in d['metadata']['name'] else
                             {'5gc.free5gc.org/role': 'ran-test'})

    def test_command_requires_actual_user_source_binding(self):
        args = tool.command({'pod': 'UE2-POD', 'ip': '10.60.0.4'}, self.spec['routes'][1], 'downlink', self.spec, '192.168.244.128')
        self.assertEqual(args[args.index('-B') + 1], '10.60.0.4')
        self.assertEqual(args[args.index('-p') + 1], '31302')
        self.assertIn('-R', args)


class RawFlowTests(unittest.TestCase):
    def setUp(self):
        self.spec = tool.config()[0]
        self.route = self.spec['routes'][0]
        name = tool.route_name(self.route)
        self.name = name
        self.data = {'start': {'test_start': {'duration': 10, 'reverse': 0}, 'cookie': 'unique-cookie',
            'timestamp': {'timesecs': 100}, 'connected': [{'local_host': '10.60.0.3', 'remote_host': '192.168.244.128',
                                                         'remote_port': 31301}]},
            'intervals': [{'sum': {'start': 0, 'end': 10, 'bytes': 1048576}}],
            'end': {'sum_sent': {'bytes': 1048576}, 'sum_received': {'bytes': 1048576, 'seconds': 10, 'bits_per_second': 838860.8}}}
        self.row = {'startedAt': '2026-10-20T00:00:00Z', 'trafficEndedAt': '2026-10-20T00:00:12Z', 'direction': 'uplink'}
        self.topology = {'users': {'ue1': {'ip': '10.60.0.3'}}, 'target': '192.168.244.128', 'envoy': {'ip': '10.42.0.1'},
                         'services': {name: {'spec': {'clusterIP': '10.43.0.1'}}}}
        self.logs = '\n'.join(json.dumps({'experiment': tool.VERSION, 'test_user': 'ue1', 'business_class': 'core',
            'listener': name, 'cluster': name, 'local': '10.42.0.1:15301', 'upstream': '10.43.0.1:5201',
            'flags': '-', 'bytes_received': size, 'start': '2026-10-20T00:00:01Z'}) for size in [100, 1048576])
        self.backend = {name: 'Accepted connection from 10.42.0.1,\nunique-cookie', 'other': ''}

    def validate(self):
        return tool.validate_flow(self.data, self.row, self.route, self.spec, self.topology, self.logs, self.backend)

    def test_complete_bound_flow_passes(self):
        self.assertEqual(self.validate()['proxyConnections'], 2)

    def test_wrong_ue_binding_rejected(self):
        self.data['start']['connected'][0]['local_host'] = '10.60.0.4'
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_wrong_destination_port_rejected(self):
        self.data['start']['connected'][0]['remote_port'] = 31302
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_early_stop_rejected(self):
        self.data['end']['sum_received']['seconds'] = 5
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_wrong_backend_cookie_rejected(self):
        self.backend['other'] = 'unique-cookie'
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_wrong_user_log_rejected(self):
        self.logs = self.logs.replace('"ue1"', '"ue2"')
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_extra_proxy_connection_rejected(self):
        self.logs += '\n' + self.logs.splitlines()[0]
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_simultaneous_positive_intervals_have_conservative_overlap(self):
        other = copy.deepcopy(self.data)
        other['start']['timestamp']['timesecs'] = 101
        self.assertEqual(tool.overlap_lower_bound([self.data, other]), 8)

    def test_serial_sessions_have_no_overlap(self):
        other = copy.deepcopy(self.data)
        other['start']['timestamp']['timesecs'] = 111
        self.assertEqual(tool.overlap_lower_bound([self.data, other]), 0)

    def test_empty_traffic_cannot_prove_concurrency(self):
        self.data['intervals'][0]['sum']['bytes'] = 0
        with self.assertRaises(RuntimeError):
            tool.active_window(self.data)


if __name__ == '__main__':
    unittest.main()
