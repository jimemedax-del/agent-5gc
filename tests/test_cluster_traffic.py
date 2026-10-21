"""Traffic boundaries and lifecycle regressions; no live cluster mutations."""
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from services.cluster_dashboard.traffic import normalize, check_routes
from services.cluster_dashboard.server import Store, handler


def config():
    return {'environment':'cnf-multi-ue','durationSeconds':30,'flows':[
        {'ue':'ue1','target':'core','direction':'ul','rateMbps':1}]}


def remote_module():
    spec = importlib.util.spec_from_file_location('traffic_remote_test',
        Path(__file__).resolve().parents[1]/'services/cluster_dashboard/traffic_remote.py')
    module = importlib.util.module_from_spec(spec)
    fake = types.SimpleNamespace(LOCK_EX=1,LOCK_NB=2,flock=lambda *args:None)
    with patch.dict('sys.modules',{'fcntl':fake}):
        spec.loader.exec_module(module)
    return module


class ConfigTests(unittest.TestCase):
    def test_route_drift_is_rejected_before_any_flow(self):
        data={'routes':[{'namespace':'cnf-multi-ue','port':15301,'target':'svc/cnf-multi-ue/cnf-a-core-ue1',
                        'resolved':True,'targets':[{'port':5201}]}],
              'services':[{'id':'svc/cnf-multi-ue/cnf-a-core-ue1','endpoints':[{'ready':True,'addresses':['10.0.1.1']}]}]}
        check_routes(config(),data)
        data['routes'][0]['target']='svc/cnf-multi-ue/cnf-a-edge-ue1'
        with self.assertRaises(ValueError):check_routes(config(),data)

    def test_limits_and_exact_schema(self):
        for mutate in (
            lambda c:c.update(durationSeconds=True),lambda c:c.update(durationSeconds=301),
            lambda c:c.update(durationSeconds=9),lambda c:c.update(durationSeconds=30.5),
            lambda c:c.update(environment='arbitrary'),lambda c:c.update(command='kubectl delete'),
            lambda c:c.update(flows=[]),lambda c:c.update(flows=c['flows']*5)):
            c=config();mutate(c)
            with self.assertRaises(ValueError):normalize(c)
        self.assertEqual(normalize(config()),config())

    def test_nonfinite_boolean_huge_and_out_of_range_speeds_rejected(self):
        for speed in (True,None,'1;rm',float('nan'),float('inf'),10**1000,0,.09,4.01):
            with self.subTest(speed=str(speed)[:20]):
                c=config();c['flows'][0]['rateMbps']=speed
                with self.assertRaises(ValueError):normalize(c)

    def test_shared_targets_and_total_limit(self):
        c=config();c['environment']='cnf-lab'
        c['flows'].append(dict(c['flows'][0],ue='ue2'))
        with self.assertRaises(ValueError):normalize(c)
        c['environment']='cnf-multi-ue'
        self.assertEqual(len(normalize(c)['flows']),2)
        for f in c['flows']:f['rateMbps']=3
        with self.assertRaises(ValueError):normalize(c)

    def test_port_and_protocol_injection_are_rejected(self):
        for key in ('port','host','command','protocol','pod','ip'):
            c=config();c['flows'][0][key]='arbitrary'
            with self.assertRaises(ValueError):normalize(c)


class WorkerTests(unittest.TestCase):
    def test_results_require_complete_metrics_and_correct_direction(self):
        remote=remote_module()
        flow={'ip':'10.60.0.14','port':31301,'direction':'ul'}
        summary={'bytes':1000,'seconds':10,'bits_per_second':800}
        result={'start':{'connected':[{'local_host':flow['ip'],'remote_port':31301}],
                         'test_start':{'reverse':0,'duration':10,'protocol':'TCP'}},
                'end':{'sum_received':dict(summary),'sum_sent':dict(summary)}}
        self.assertTrue(remote.valid_result(result,flow,10))
        for mode in ('missing','zero','nan','wrong-ip','wrong-direction','wrong-duration','empty'):
            r=copy.deepcopy(result)
            if mode=='missing':del r['end']['sum_received']
            elif mode=='zero':r['end']['sum_received']['bytes']=0
            elif mode=='nan':r['end']['sum_received']['bits_per_second']=float('nan')
            elif mode=='wrong-ip':r['start']['connected'][0]['local_host']='another'
            elif mode=='wrong-direction':r['start']['test_start']['reverse']=1
            elif mode=='wrong-duration':r['start']['test_start']['duration']=99
            else:r['start']['connected']=[]
            with self.subTest(mode=mode):self.assertFalse(remote.valid_result(r,flow,10))

    def test_exception_cleanup_keeps_experiment_lock_open_and_retries(self):
        remote=remote_module()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'.work').mkdir()
            remote.BASE=root/'jobs';remote.BASE.mkdir()
            directory=remote.job_dir('a'*32);directory.mkdir()
            remote.write(directory/'state.json',dict(id='a'*32,status='starting',config=config(),results=[],segment=0))
            handles=[];calls=[]
            def flock(handle,flags):handles.append(handle)
            def cleanup(*args):
                calls.append(True)
                self.assertFalse(handles[0].closed)
                return ['temporarily unreachable'] if len(calls)==1 else []
            with patch.object(remote.Path,'home',return_value=root),patch.object(remote.fcntl,'flock',side_effect=flock),\
                    patch.object(remote,'resolve',side_effect=RuntimeError('preflight failed')),\
                    patch.object(remote,'cleanup',side_effect=cleanup),patch.object(remote.time,'sleep'):
                remote.worker('a'*32)
            self.assertEqual(len(calls),2)
            self.assertTrue(handles[0].closed)
            self.assertEqual(remote.load(directory/'state.json')['status'],'failed')

    def test_unknown_recovery_is_never_marked_completed(self):
        remote=remote_module()
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);remote.write(d/'state.json',dict(id='a'*32,status='running',resolved=[]))
            with patch.object(remote,'alive',return_value=False),patch.object(remote,'cleanup',return_value=['unreachable']):
                info=remote.reconcile(d)
                self.assertEqual(info['status'],'unknown')
                self.assertIsNone(info['finishedAt'])
            with patch.object(remote,'alive',return_value=False),patch.object(remote,'cleanup',return_value=[]):
                self.assertEqual(remote.reconcile(d)['status'],'interrupted')

    def test_replaced_pod_does_not_count_as_confirmed_cleanup(self):
        remote=remote_module()
        with patch.object(remote,'kubectl',return_value=json.dumps({'metadata':{'uid':'new'}})):
            errors=remote.cleanup(None,{'id':'a'*32,'resolved':[{'pod':'ue','podUid':'old'}]})
        self.assertTrue(errors)


class FacadeTests(unittest.TestCase):
    def test_replay_and_stale_snapshots_block_start(self):
        for source in ('replay','ssh'):
            store=Store(source=source);store.data={'observedAt':'2020-01-01T00:00:00Z','checks':[]}
            with patch.object(store.traffic,'rpc') as rpc, self.assertRaises(ValueError):
                store.traffic.start(config())
            rpc.assert_not_called()

    def test_failed_refresh_preserves_active_task(self):
        traffic=Store().traffic
        traffic.cache['jobs']=[{'id':'a'*32,'status':'running'}]
        with patch.object(traffic,'rpc',side_effect=TimeoutError('SSH unavailable')):traffic.refresh()
        self.assertEqual(traffic.state()['jobs'][0]['status'],'running')
        self.assertFalse(traffic.state()['available'])

    def test_cache_reads_do_not_wait_for_remote_operation(self):
        traffic=Store().traffic
        with traffic.operation:
            self.assertIsNone(traffic.state()['error'])
            traffic.refresh()  # Non-blocking skip.

    def test_report_exports_displayed_evidence_even_when_ssh_is_down(self):
        traffic=Store().traffic
        traffic.cache.update(jobs=[{'id':'a'*32,'status':'completed','results':[{'receiverMbps':1}]}],
                             observedAt=100,error='SSH unavailable',available=False)
        with patch.object(traffic,'rpc') as rpc:
            report=traffic.report('a'*32)
        rpc.assert_not_called()
        self.assertTrue(report['cached'])
        self.assertEqual(report['observedAt'],100)
        self.assertEqual(report['job']['results'][0]['receiverMbps'],1)
        self.assertEqual(report['connectionError'],'SSH unavailable')


class HttpTrafficTests(unittest.TestCase):
    def setUp(self):
        self.store=Store();self.store.data={'observedAt':dt.datetime.now(dt.timezone.utc).isoformat(),'checks':[]}
        self.store.data.update(routes=[{'namespace':'cnf-multi-ue','port':15301,
            'target':'svc/cnf-multi-ue/cnf-a-core-ue1','resolved':True,'targets':[{'port':5201}]}],
            services=[{'id':'svc/cnf-multi-ue/cnf-a-core-ue1','endpoints':[{'ready':True,'addresses':['10.0.1.1']}]}])
        self.server=ThreadingHTTPServer(('127.0.0.1',0),lambda *args:None)
        self.port=self.server.server_port;self.server.RequestHandlerClass=handler(self.store,self.port)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
    def post(self,payload,headers=None,path='/api/traffic/start'):
        h={'Content-Type':'application/json','X-Traffic-Token':self.store.traffic.token}
        h.update(headers or {})
        return urlopen(Request(self.base+path,data=json.dumps(payload).encode(),headers=h,method='POST'))
    def test_mutations_require_token_origin_and_same_site(self):
        for headers in ({'X-Traffic-Token':'bad'},{'Origin':'https://outside.example'},{'Sec-Fetch-Site':'cross-site'}):
            with patch.object(self.store.traffic,'rpc') as rpc, self.assertRaises(HTTPError) as ctx:
                self.post(config(),headers)
            self.assertEqual(ctx.exception.code,403);rpc.assert_not_called()
    def test_oversize_wrong_type_and_unknown_keys_rejected(self):
        for payload,headers in (({'padding':'x'*9000},{}),(config(),{'Content-Type':'text/plain'}),({'command':'x'},{})):
            with patch.object(self.store.traffic,'rpc') as rpc,self.assertRaises(HTTPError) as ctx:
                self.post(payload,headers)
            self.assertEqual(ctx.exception.code,400);rpc.assert_not_called()
    def test_start_and_duplicate_are_serialized(self):
        with patch.object(self.store.traffic,'rpc',return_value={'job':{'id':'a'*32,'status':'starting'}}) as rpc:
            with self.post(config()) as response:self.assertEqual(response.status,202)
            with self.assertRaises(HTTPError) as ctx:self.post(config())
            self.assertEqual(ctx.exception.code,409);self.assertEqual(rpc.call_count,1)
    def test_stop_accepts_only_job_id(self):
        with patch.object(self.store.traffic,'rpc') as rpc,self.assertRaises(HTTPError) as ctx:
            self.post({'id':'../private'},path='/api/traffic/stop')
        self.assertEqual(ctx.exception.code,400);rpc.assert_not_called()
    def test_replay_never_invokes_remote_mutation(self):
        self.store.source='replay'
        with patch.object(self.store.traffic,'rpc') as rpc,self.assertRaises(HTTPError) as ctx:self.post(config())
        self.assertEqual(ctx.exception.code,400);rpc.assert_not_called()


if __name__=='__main__':unittest.main()
