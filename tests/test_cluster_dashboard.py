"""Evidence-sensitive regression checks for the read-only observatory."""
import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from services.cluster_dashboard.collect import access_bindings, sanitize
from services.cluster_dashboard.model import build, quantity, rate
from services.cluster_dashboard.server import Store, handler


AT = '2026-10-21T12:00:00+00:00'


def resource(kind, name, namespace='lab', **kwargs):
    return dict(kind=kind, metadata={'name': name, 'namespace': namespace, 'uid': name + '-uid', 'labels': {}}, **kwargs)


def fixture():
    pod = resource('Pod', 'envoy-1', spec={'nodeName': 'node', 'containers': [{'name': 'envoy', 'image': 'image'}],
                                        'configMaps': ['active']},
                   status={'phase': 'Running', 'podIP': '10.0.0.2', 'conditions': [{'type': 'Ready', 'status': 'True'}],
                           'containerStatuses': [{'name': 'envoy', 'ready': True, 'restartCount': 0}]})
    target = resource('Pod', 'backend', spec={'nodeName': 'node', 'containers': [{'name': 'app'}]},
                      status={'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}]})
    service = resource('Service', 'backend', spec={'selector': {'app': 'backend'}, 'ports': [{'port': 5201}]})
    ep = resource('EndpointSlice', 'slice', endpoints=[{'conditions': {'ready': True},
                  'addresses': ['10.0.0.3'], 'targetRef': {'name': 'backend', 'namespace': 'lab'}}])
    ep['metadata']['labels']['kubernetes.io/service-name'] = 'backend'
    return {'resources': [pod, target, service, ep], 'apiReady': 'ok', 'observedAt': AT, 'startedAt': AT,
            'durationSeconds': 1, 'errors': {}, 'telemetry': [],
            'proxyConfig': {'routes': [{'namespace': 'lab', 'configMap': 'active', 'cluster': 'a',
                                      'listener': 'a', 'port': 15201,
                                      'targets': [{'host': 'backend.lab.svc.cluster.local.', 'port': 5201}]}]}}


class ModelTests(unittest.TestCase):
    def test_quantity_understands_real_metric_units(self):
        self.assertEqual(quantity('500m'), .5)
        self.assertEqual(quantity('250000000n'), .25)
        self.assertEqual(quantity('2Gi'), 2 * 1024**3)
        self.assertIsNone(quantity('unknown'))

    def test_rate_requires_same_identity_and_monotonic_counter(self):
        old = {'at': AT, 'uid': 'a', 'bytes': 100}
        cur = {'at': '2026-10-21T12:00:10+00:00', 'uid': 'a', 'bytes': 1250100}
        self.assertEqual(rate(cur, old, 'bytes'), 1)
        self.assertIsNone(rate(cur, None, 'bytes'))
        self.assertIsNone(rate(dict(cur, uid='b'), old, 'bytes'))
        self.assertIsNone(rate(dict(cur, bytes=50), old, 'bytes'))
        self.assertIsNone(rate(dict(cur, at=AT), old, 'bytes'))
        self.assertIsNone(rate(dict(cur, at='2026-10-21T12:03:00+00:00'), old, 'bytes'))

    def test_missing_or_reset_metrics_are_not_zero(self):
        data = build(fixture())
        self.assertIsNone(data['pods'][0]['cpuUsed'])
        self.assertIsNone(data['routes'][0]['rxMbps'])
        self.assertFalse(data['routes'][0]['statsAvailable'])

    def test_only_mounted_proxy_configs_form_routes(self):
        raw = fixture()
        old = dict(raw['proxyConfig']['routes'][0], configMap='unused-history')
        raw['proxyConfig']['routes'].append(old)
        data = build(raw)
        self.assertEqual(len(data['routes']), 1)
        self.assertEqual(data['routes'][0]['configMap'], 'active')

    def test_endpoints_are_bound_by_namespace_and_slice_labels(self):
        raw = fixture()
        wrong = copy.deepcopy(raw['resources'][-1])
        wrong['metadata']['namespace'] = 'other'
        raw['resources'].append(wrong)
        data = build(raw)
        self.assertEqual(len(data['services'][0]['endpoints']), 1)
        self.assertEqual(data['services'][0]['endpoints'][0]['target'], 'pod/lab/backend')

    def test_missing_ready_endpoints_fail_health_check(self):
        raw = fixture()
        raw['resources'][-1]['endpoints'][0]['conditions']['ready'] = False
        data = build(raw)
        self.assertEqual(next(c for c in data['checks'] if c['key'] == 'endpoints')['status'], 'fail')

    def test_replaced_pod_is_not_bound_to_an_old_endpoint_uid(self):
        raw = fixture()
        raw['resources'][-1]['endpoints'][0]['targetRef']['uid'] = 'old-uid'
        data = build(raw)
        self.assertIsNone(data['services'][0]['endpoints'][0]['target'])

    def test_route_identity_does_not_depend_on_list_position(self):
        raw = fixture()
        before = build(raw)['routes'][0]['id']
        raw['proxyConfig']['routes'].insert(0, dict(raw['proxyConfig']['routes'][0], listener='another'))
        after = build(raw)['routes'][1]['id']
        self.assertEqual(before, after)

    def test_old_interface_counters_are_reported_as_unknown(self):
        raw = fixture()
        raw['telemetry'] = [{'id': 'lab/envoy-1', 'uid': 'envoy-1-uid',
                             'at': '2026-10-21T11:55:00+00:00', 'rxBytes': 100, 'txBytes': 100}]
        data = build(raw)
        check = next(c for c in data['checks'] if c['key'] == 'telemetry')
        self.assertEqual(check['status'], 'unknown')
        self.assertIn('120', check['detail'])

    def test_network_policy_objects_do_not_claim_effective_enforcement(self):
        raw = fixture()
        raw['resources'].append(resource('NetworkPolicy', 'isolate', spec={'podSelector': {}}))
        data = build(raw)
        self.assertEqual(len(data['policies']), 1)
        self.assertNotIn('enforced', data['policies'][0])

    def test_proxy_rates_use_admin_sample_clock_and_preserve_zero(self):
        raw = fixture()
        old = {'telemetry': [{'id': 'lab/envoy-1', 'uid': 'envoy-1-uid', 'at': AT, 'envoyAt': AT,
                             'envoy': {'cluster.a.upstream_cx_rx_bytes_total': 10,
                                       'cluster.a.upstream_cx_tx_bytes_total': 0}}]}
        raw['telemetry'] = [{'id': 'lab/envoy-1', 'uid': 'envoy-1-uid', 'at': AT,
                             'envoyAt': '2026-10-21T12:00:10+00:00',
                             'envoy': {'cluster.a.upstream_cx_rx_bytes_total': 1250010,
                                       'cluster.a.upstream_cx_tx_bytes_total': 0}}]
        data = build(raw, old)
        self.assertEqual(data['routes'][0]['rxMbps'], 1)
        self.assertEqual(data['routes'][0]['txMbps'], 0)

    def test_sanitize_does_not_return_environment_secret_refs_or_annotations(self):
        pod = resource('Pod', 'pod', spec={'containers': [{'name': 'c', 'image': 'safe',
                       'env': [{'name': 'KEY', 'value': 'sensitive'}]}], 'volumes': [{'secret': {'secretName': 'key'}}]}, status={})
        pod['metadata']['annotations'] = {'private': 'sensitive'}
        result = json.dumps(sanitize([pod]))
        self.assertNotIn('sensitive', result)
        self.assertNotIn('secretName', result)
        self.assertNotIn('annotations', result)


def activity_fixture():
    raw = fixture()
    raw['proxyConfig']['routes'][0]['statPrefix'] = 'entry'
    for name in ('ueransim-ue-one', 'cnf-a-ue2-two', 'ueransim-gnb-one', 'free5gc-upf-one'):
        raw['resources'].append(resource('Pod', name, spec={'containers': []},
            status={'phase': 'Running', 'podIP': '10.0.1.' + str(len(raw['resources'])),
                    'conditions': [{'type': 'Ready', 'status': 'True'}]}))
    raw['resources'].append(resource('Service', 'gnb', spec={}))
    ep = resource('EndpointSlice', 'gnb-slice', endpoints=[{'conditions': {'ready': True},
        'targetRef': {'name': 'ueransim-gnb-one', 'uid': 'ueransim-gnb-one-uid'}}])
    ep['metadata']['labels']['kubernetes.io/service-name'] = 'gnb'
    raw['resources'].append(ep)
    raw['proxyConfig']['accessBindings'] = [{'podUid': n + '-uid', 'targets': ['gnb']}
        for n in ('ueransim-ue-one', 'cnf-a-ue2-two')]
    for name, interface, ip in [('ueransim-ue-one', 'uesimtun0', ['10.60.0.1']),
                               ('cnf-a-ue2-two', 'uesimtun0', ['10.60.0.2']),
                               ('free5gc-upf-one', 'upfgtp', [])]:
        raw['telemetry'].append({'id': 'lab/' + name, 'uid': name + '-uid', 'at': AT,
            'interfaceStats': [{'uid': name + ':3:' + ','.join(ip), 'podUid': name + '-uid',
                'name': interface, 'index': 3, 'addresses': ip, 'at': AT, 'rxBytes': 100, 'txBytes': 100}]})
    raw['telemetry'].append({'id': 'lab/envoy-1', 'uid': 'envoy-1-uid', 'at': AT, 'envoyAt': AT,
        'envoy': {k: 100 for k in ('tcp.entry.downstream_cx_rx_bytes_total',
            'tcp.entry.downstream_cx_tx_bytes_total', 'cluster.a.upstream_cx_rx_bytes_total',
            'cluster.a.upstream_cx_tx_bytes_total')}})
    cur = copy.deepcopy(raw)
    cur['observedAt'] = '2026-10-21T12:00:10+00:00'
    for t in cur['telemetry']:
        t['at'] = cur['observedAt']
        for i in t.get('interfaceStats', []):
            i['at'] = cur['observedAt']
        if 'envoyAt' in t:
            t['envoyAt'] = cur['observedAt']
    return raw, cur


class ActivityTests(unittest.TestCase):
    def test_direction_and_ue_isolation(self):
        for reverse in (False, True):
            old, cur = activity_fixture()
            cur['telemetry'][0]['interfaceStats'][0]['rxBytes' if reverse else 'txBytes'] += 1250000
            cur['telemetry'][2]['interfaceStats'][0]['txBytes' if reverse else 'rxBytes'] += 1250000
            cur['telemetry'][3]['envoy']['tcp.entry.downstream_cx_' + ('tx' if reverse else 'rx') + '_bytes_total'] += 1250000
            links = build(cur, old)['trafficLinks']
            ue1 = next(l for l in links if l['source'].endswith('/ueransim-ue-one'))
            ue2 = next(l for l in links if l['source'].endswith('/cnf-a-ue2-two'))
            self.assertEqual(ue1['status'], 'active')
            self.assertEqual(ue2['status'], 'idle')
            for link in [ue1] + [l for l in links if l['segment'] != 'access']:
                self.assertEqual(link['status'], 'active')
                self.assertEqual(link['forwardActive'], not reverse)
                self.assertEqual(link['reverseActive'], reverse)
                self.assertNotIn('rxMbps', link)

    def test_backend_alone_does_not_light_front_links(self):
        old, cur = activity_fixture()
        cur['telemetry'][3]['envoy']['cluster.a.upstream_cx_tx_bytes_total'] += 1250000
        model = build(cur, old)
        self.assertEqual(model['routes'][0]['txMbps'], 1)
        self.assertTrue(all(l['status'] == 'idle' for l in model['trafficLinks']))

    def test_first_missing_reset_replaced_or_stale_sample_is_unknown(self):
        for mode in ('first', 'missing', 'reset', 'pod', 'interface', 'ip', 'stale', 'nonoverlap'):
            with self.subTest(mode=mode):
                old, cur = activity_fixture()
                i = cur['telemetry'][0]['interfaceStats'][0]
                if mode == 'first': old = None
                elif mode == 'missing': cur['telemetry'][0]['interfaceStats'] = []
                elif mode == 'reset': i['txBytes'] = 0
                elif mode == 'pod': i['podUid'] = 'replaced'
                elif mode in ('interface', 'ip'): i['uid'] += '-changed'
                elif mode == 'stale': cur['observedAt'] = '2026-10-21T12:03:00+00:00'
                elif mode == 'nonoverlap':
                    old['telemetry'][0]['interfaceStats'][0]['at'] = '2026-10-21T11:59:40+00:00'
                    i['at'] = '2026-10-21T11:59:50+00:00'
                model = build(cur, old)
                n3 = next(l for l in model['trafficLinks'] if l['segment'] == 'n3')
                self.assertEqual(n3['status'], 'unknown')
                self.assertFalse(n3['forwardActive'])

    def test_unresolved_or_ambiguous_gnb_is_not_assumed(self):
        old, cur = activity_fixture()
        cur['proxyConfig']['accessBindings'] = []
        self.assertEqual(build(cur, old)['trafficLinks'], [])
        old, cur = activity_fixture()
        cur['resources'][-1]['endpoints'][0]['conditions']['ready'] = False
        self.assertEqual(build(cur, old)['trafficLinks'], [])
        old, cur = activity_fixture()
        other = copy.deepcopy(cur['resources'][-4])
        other['metadata']['name'] = 'ueransim-gnb-other'
        other['metadata']['uid'] = 'another-gnb'
        cur['resources'].append(other)
        self.assertTrue(all(l['segment'] == 'access' for l in build(cur, old)['trafficLinks']))

    def test_access_binding_excludes_authentication_fields(self):
        class Parser:
            @staticmethod
            def safe_load(value):
                return json.loads(value)
        pod = resource('Pod', 'ueransim-ue-one', spec={'volumes': [{'configMap': {'name': 'ue'}}]})
        with patch('services.cluster_dashboard.collect.get', return_value={'data': {'ue.yaml':
                   json.dumps({'gnbSearchList': ['gnb'], 'key': 'private-key', 'supi': 'private-id'})}}):
            result = json.dumps(access_bindings([pod], Parser))
        self.assertIn('gnb', result)
        self.assertNotIn('private', result)


class StoreTests(unittest.TestCase):
    def test_windows_collection_does_not_create_console_windows(self):
        for source in ('ssh', 'local'):
            with self.subTest(source=source), \
                    patch('services.cluster_dashboard.server.sys.platform', 'win32'), \
                    patch('services.cluster_dashboard.server.subprocess.CREATE_NO_WINDOW', 0x08000000, create=True), \
                    patch('services.cluster_dashboard.server.subprocess.run') as run:
                run.return_value.returncode = 0
                run.return_value.stdout = json.dumps(fixture())
                store = Store(source=source)
                store.collect()
                self.assertIsNone(store.state()['error'])
                self.assertEqual(run.call_args.kwargs['creationflags'], 0x08000000)

    def test_collection_failure_retains_previous_evidence_and_marks_error(self):
        store = Store()
        store.data = {'observedAt': AT}
        with patch('services.cluster_dashboard.server.subprocess.run', side_effect=TimeoutError('SSH timeout')):
            store.collect()
        self.assertEqual(store.state()['data']['observedAt'], AT)
        self.assertIn('SSH timeout', store.state()['error'])
        self.assertFalse(store.state()['busy'])

    def test_duplicate_collection_does_not_start_parallel_remote_operations(self):
        store = Store()
        store.busy = True
        with patch('services.cluster_dashboard.server.subprocess.run') as run:
            store.collect()
        run.assert_not_called()

    def test_refresh_completion_requires_a_new_collection_attempt(self):
        store = Store()
        target = store.request_refresh()
        self.assertEqual(target, 1)
        self.assertLess(store.state()['completedAttempt'], target)
        with patch('services.cluster_dashboard.server.subprocess.run', side_effect=TimeoutError('timeout')):
            store.collect()
        self.assertEqual(store.state()['completedAttempt'], target)
        self.assertIsNotNone(store.state()['error'])

    def test_replay_does_not_fabricate_rate_or_new_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.json'
            path.write_text(json.dumps(fixture()), encoding='utf-8')
            store = Store('replay', snapshot_file=path)
            store.collect()
            store.collect()
            result = store.state()
            self.assertEqual(result['source'], 'replay')
            self.assertEqual(result['data']['observedAt'], AT)
            self.assertEqual(len(result['history']), 1)
            self.assertIsNone(result['history'][0]['rxMbps'])


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), lambda *args: None)
        self.port = self.server.server_port
        self.server.RequestHandlerClass = handler(self.store, self.port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_snapshot_and_assets_have_no_external_dependencies(self):
        with urlopen(self.base + '/api/state') as response:
            self.assertIsNone(json.load(response)['data'])
            self.assertIn("default-src 'self'", response.headers['Content-Security-Policy'])
        with urlopen(self.base + '/') as response:
            self.assertIn('网络观测台', response.read().decode('utf-8'))

    def test_refresh_enqueues_only_fixed_collection(self):
        with urlopen(Request(self.base + '/api/refresh', data=b'', method='POST')) as response:
            self.assertEqual(response.status, 202)
        self.assertTrue(self.store.wake.is_set())

    def test_cross_origin_and_unexpected_host_cannot_use_local_access(self):
        for headers in ({'Origin': 'https://outside.example'}, {'Host': 'outside.example'}):
            with self.assertRaises(HTTPError) as ctx:
                urlopen(Request(self.base + '/api/refresh', data=b'', headers=headers, method='POST'))
            self.assertEqual(ctx.exception.code, 403)
        self.assertFalse(self.store.wake.is_set())

    def test_arbitrary_command_parameters_are_rejected(self):
        with self.assertRaises(HTTPError) as ctx:
            urlopen(Request(self.base + '/api/refresh', data=b'{"command":"delete"}', method='POST'))
        self.assertEqual(ctx.exception.code, 400)
        self.assertFalse(self.store.wake.is_set())

    def test_path_traversal_and_mutation_endpoint_are_unavailable(self):
        for path in ('/../collect.py', '/api/execute'):
            with self.assertRaises(HTTPError) as ctx:
                urlopen(self.base + path)
            self.assertEqual(ctx.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
