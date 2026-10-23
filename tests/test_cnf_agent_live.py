"""F3a integration gates using the real E3 planner and fake network execution."""
import copy
from datetime import datetime, timezone, timedelta
from contextlib import redirect_stdout, redirect_stderr
import io
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

from services.cnf_agent import live_worker
from services.cnf_agent.live_contracts import OperationSnapshot, PolicyDemand, PolicyExtraction, PolicyReply
from services.cnf_agent.live_requirements import assess
from services.cnf_agent.live_tools import LiveTools, operation_result
from services.cnf_agent.live_transport import SshTransport, TransportError, bundle
from services.cnf_agent.live_workflow import LiveWorkflow
from services.cnf_agent.memory import Memory
from services.cnf_agent.schemas import Message, Phase, Source
from services.cnf_agent.trace import summary


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('agent_e3_tests', ROOT/'scripts/cnf-planner-policy.py')
E3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E3)
AT = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


def demand_set(high=None):
    common = 'C1受控下行竞争实验；双方UDP下行，实验30秒，测量窗口20秒。'
    requests, paragraphs = [], [common]
    for slot in ('ue1', 'ue2'):
        minimum, maximum, priority = ((5.0, 8.0, 'high') if slot == high else (1.5, 4.0, 'low')) if high else (4.0, 8.0, 'normal')
        priority_word = {'high': '高优先级', 'low': '低优先级', 'normal': '普通优先级'}[priority]
        text = f'{slot.upper()}{priority_word}，带宽最低{minimum} Mbps，上限{maximum} Mbps。'
        paragraphs.append(text)
        values = dict(user_slot=slot, direction='downlink', protocol='UDP', min_bandwidth_mbps=minimum,
                      max_bandwidth_mbps=maximum, priority=priority, valid_for_seconds=30, measurement_window_seconds=20)
        quotes = dict(user_slot=slot.upper(), direction='下行', protocol='UDP',
                      min_bandwidth_mbps=f'最低{minimum} Mbps', max_bandwidth_mbps=f'上限{maximum} Mbps',
                      priority=priority_word, valid_for_seconds='实验30秒', measurement_window_seconds='测量窗口20秒')
        sources = {key: Source(message_id='m1', quote=value) for key, value in quotes.items()}
        requests.append(PolicyDemand(**values, sources=sources))
    extraction = PolicyExtraction(scope='controlled_c1_downlink', scope_source=Source(message_id='m1', quote='C1受控下行竞争实验'), requests=tuple(requests))
    return extraction, ''.join(paragraphs)


class Model:
    def __init__(self, extraction):
        self.extraction = extraction

    def extract_policy(self, messages, max_tokens):
        return PolicyReply(extraction=self.extraction, tokens=80)


class Transport:
    release_id = 'a'*64

    def __init__(self):
        self.calls, self.payload, self.operation_id = [], None, None
        self.running = True
        self.lose_reply = False
        self.restore = True
        self.tamper_result = False
        self.fail_measurement = False
        self.state = E3.live.live_state({'queueMode':'none', 'endpoint':{'uid':'endpoint'}, 'bindings':{
            slot: {'supi':'supi-'+slot, 'session_id':'pdu-'+slot} for slot in ('ue1','ue2')}})

    def call(self, command, arguments):
        self.calls.append(command)
        if command == 'inspect':
            return self.state
        if command == 'preview':
            return E3.plan_policy(arguments['state'], arguments['batch'])
        if command == 'execute':
            self.operation_id = arguments['operation_id']
            self.payload = {key: arguments[key] for key in ('state','batch','plan')}
            if self.lose_reply:
                raise TransportError('reply lost')
        if self.running:
            return dict(operation_id=self.operation_id, status='running', result=None, evidence='vm1/operation')
        plan = self.payload['plan']
        mode = plan['innerPlan']['mode']
        rates = {'equal':(4.82,4.82), 'guarantee-ue1':(7.72,1.93), 'guarantee-ue2':(1.93,7.72)}[mode]
        goals = {request['user_id']:request['requirements'] for request in self.payload['batch']['requests']}
        comparisons = {}
        for i, slot in enumerate(('ue1','ue2')):
            goal = goals['supi-'+slot]
            comparisons[slot] = dict(receivedSteadyMbps=rates[i], minimumMbps=goal['min_bandwidth_mbps'],
                                     maximumMbps=goal['max_bandwidth_mbps'], passed=True)
        if self.tamper_result:
            comparisons['ue1']['minimumMbps'] = 0
        outcome = 'failed' if self.fail_measurement else 'passed_controlled_targets'
        raw = dict(payload_hash=live_worker.digest(self.payload), plan_ref=plan['policyPlanHash'],
                   summary=dict(outcome=outcome, finalStrictBaselinePassed=self.restore,
                       applyCompleted=True, original26PodIdentitiesUnchanged=True,
                       mutationAttempted=True, restoration='restored' if self.restore else 'failed',
                       selectedMode=mode, policyPlanId=plan['policyPlanId']),
                   verification=dict(status='passed_controlled_targets', policyPlanHash=plan['policyPlanHash'],
                                     evidenceIntegrity='passed', historicalFixtureChecks={'passed':True},
                                     latencyLossGuaranteesAccepted=False,
                                     targetComparisons=comparisons))
        return dict(operation_id=self.operation_id, status='completed', result=raw, evidence='vm1/operation')


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = Memory(Path(self.temp.name)/'agent.sqlite')
        self.addCleanup(self.memory.close)
        for target in (E3.live, E3.live.p):
            patcher = patch.object(target, 'now' if target is E3.live else 'clock', return_value=AT)
            patcher.start()
            self.addCleanup(patcher.stop)

    def start(self, extraction=None, text=None, transport=None):
        normal, message = demand_set()
        transport = transport or Transport()
        workflow = LiveWorkflow(self.memory, Model(extraction or normal), LiveTools(transport), now=lambda:AT)
        session = workflow.create()
        return workflow, transport, workflow.advance(session.id, text or message)

    def test_three_modes_use_e3_bound_requests_and_actual_result_comparisons(self):
        for high, mode in [(None,'equal'),('ue1','guarantee-ue1'),('ue2','guarantee-ue2')]:
            extraction, text = demand_set(high)
            workflow, transport, session = self.start(extraction,text)
            self.assertEqual(session.phase,Phase.EXECUTING)
            self.assertEqual(session.plan.selected_mode,mode)
            self.assertEqual(session.plan.batch['requests'][0]['user_id'],'supi-ue1')
            transport.running=False
            done=workflow.advance(session.id)
            self.assertEqual(done.phase,Phase.SUCCEEDED)
            self.assertEqual(done.result.mode,'live')
            self.assertEqual(done.usage.experiments,1)
            self.assertEqual(len(done.result.checks),2)
            self.assertIn('受控实网实验通过',summary(done))
            self.assertEqual(workflow.advance(done.id).revision,done.revision)
            self.assertEqual(transport.calls.count('execute'),1)

    def test_missing_conflicting_and_unproven_sources_stop_before_network(self):
        req,text=demand_set()
        cases=[req.model_copy(update={'scope':None,'scope_source':None}),
               req.model_copy(update={'ambiguities':('未明确',)}),
               req.model_copy(update={'requests':(req.requests[0],)}),
               req.model_copy(update={'scope_source':Source(message_id='m2',quote='不存在')})]
        for item in cases:
            with self.subTest(item=item):
                _,transport,session=self.start(item,text)
                self.assertIn(session.phase,(Phase.WAITING,Phase.REJECTED))
                self.assertEqual(transport.calls,[])

    def test_rate_semantics_and_scope_cannot_be_granted_by_model_assertion(self):
        req,text=demand_set()
        sources=dict(req.requests[0].sources)
        sources['min_bandwidth_mbps']=sources['max_bandwidth_mbps']
        wrong=req.requests[0].model_copy(update={'sources':sources})
        for value in [req.model_copy(update={'scope_source':Source(message_id='m1',quote='UE1')}),
                      req.model_copy(update={'requests':(wrong,req.requests[1])})]:
            _,transport,session=self.start(value,text)
            self.assertEqual(session.phase,Phase.WAITING)
            self.assertEqual(transport.calls,[])

    def test_over_capacity_is_rejected_by_e3_without_any_execution(self):
        req,text=demand_set()
        text=text.replace('最低4.0 Mbps','最低6.0 Mbps')
        goals=[]
        for goal in req.requests:
            sources=dict(goal.sources,min_bandwidth_mbps=Source(message_id='m1',quote='最低6.0 Mbps'))
            goals.append(goal.model_copy(update={'min_bandwidth_mbps':6.0,'sources':sources}))
        _,transport,session=self.start(req.model_copy(update={'requests':tuple(goals)}),text)
        self.assertEqual(session.phase,Phase.REJECTED)
        self.assertEqual(transport.calls,['inspect','preview'])
        self.assertIsNone(session.operation_id)

    def test_lost_execute_reply_only_queries_existing_operation(self):
        transport=Transport();transport.lose_reply=True
        workflow,transport,session=self.start(transport=transport)
        self.assertEqual(session.phase,Phase.EXECUTING)
        self.assertEqual(transport.calls.count('execute'),1)
        transport.running=False
        result=workflow.advance(session.id)
        self.assertEqual(result.phase,Phase.SUCCEEDED)
        self.assertEqual(transport.calls.count('execute'),1)

    def test_result_query_works_after_restart_without_any_model_or_key(self):
        _,transport,session=self.start()
        transport.running=False
        with Memory(Path(self.temp.name)/'agent.sqlite') as reopened:
            workflow=LiveWorkflow(reopened,None,LiveTools(transport),now=lambda:AT)
            done=workflow.advance(session.id)
        self.assertEqual(done.phase,Phase.SUCCEEDED)
        self.assertEqual(done.usage.model_calls,1)

    def test_corrupt_results_measurement_and_restore_failure_never_become_success(self):
        for field, phase in [('tamper_result',Phase.UNKNOWN),('restore',Phase.RESTORE_FAILED),('fail_measurement',Phase.FAILED)]:
            workflow,transport,session=self.start()
            transport.running=False
            setattr(transport,field,False if field=='restore' else True)
            done=workflow.advance(session.id)
            self.assertEqual(done.phase,phase)
            if phase in (Phase.UNKNOWN,Phase.RESTORE_FAILED):
                with self.assertRaises(ValueError):workflow.advance(done.id,'another request')
            self.assertEqual(transport.calls.count('execute'),1)

    def test_incomplete_verification_cannot_inherit_a_success_label(self):
        _,transport,session=self.start()
        transport.running=False
        raw=transport.call('result',{})
        for section,key,value in [
            ('summary','applyCompleted',False),('summary','original26PodIdentitiesUnchanged',False),
            ('summary','errorCode','measurement_failed'),
            ('verification','evidenceIntegrity','failed'),('verification','historicalFixtureChecks',None),
            ('verification','latencyLossGuaranteesAccepted',True),
        ]:
            with self.subTest(key=key):
                changed=copy.deepcopy(raw)
                changed['result'][section][key]=value
                result=operation_result(OperationSnapshot.model_validate(changed),session.plan,session.observation)
                self.assertEqual(result.outcome,'failed')

    def test_malformed_stale_and_misbound_plans_never_reach_execute(self):
        for change in ('nested','expiry','version','session','release'):
            class ChangedTools(LiveTools):
                def preview_policy(self, requirement, version, observation):
                    preview=super().preview_policy(requirement,version,observation)
                    plan=preview.plan
                    if change=='nested':
                        plan=plan.model_copy(update={'policy':dict(plan.policy,innerPlan=None)})
                    elif change=='expiry':
                        expiry=AT-timedelta(seconds=1)
                        plan=plan.model_copy(update={'expires_at':expiry,'policy':dict(plan.policy,validUntil=expiry.isoformat())})
                    elif change=='version':
                        plan=plan.model_copy(update={'requirement_version':version+1})
                    elif change=='release':
                        plan=plan.model_copy(update={'release_id':'b'*64})
                    else:
                        batch=copy.deepcopy(plan.batch)
                        batch['requests'][0]['session_id']='old-session'
                        plan=plan.model_copy(update={'batch':batch})
                    return preview.model_copy(update={'plan':plan})
            req,text=demand_set()
            transport=Transport()
            workflow=LiveWorkflow(self.memory,Model(req),ChangedTools(transport),now=lambda:AT)
            session=workflow.advance(workflow.create().id,text)
            self.assertEqual(session.phase,Phase.REJECTED,change)
            self.assertNotIn('execute',transport.calls)
            self.assertIsNone(session.operation_id)

    def test_expired_observation_stops_before_preview_or_execute(self):
        transport=Transport()
        transport.state['observedAt']=(AT-timedelta(seconds=181)).isoformat()
        transport.state['validUntil']=(AT-timedelta(seconds=1)).isoformat()
        _,transport,session=self.start(transport=transport)
        self.assertEqual(session.phase,Phase.REJECTED)
        self.assertEqual(transport.calls,['inspect'])

    def test_missing_priority_can_be_completed_as_a_new_requirement_version(self):
        req,text=demand_set()
        goals=tuple(goal.model_copy(update={'priority':None,'sources':{
            k:v for k,v in goal.sources.items() if k!='priority'}}) for goal in req.requests)
        first=req.model_copy(update={'requests':goals})
        workflow,transport,session=self.start(first,text.replace('普通优先级',''))
        self.assertEqual(session.phase,Phase.WAITING)
        self.assertEqual(transport.calls,[])
        goals=tuple(goal.model_copy(update={'sources':{k:s.model_copy(update={'message_id':'m2'})
                    for k,s in goal.sources.items()}}) for goal in req.requests)
        workflow.provider.extraction=req.model_copy(update={'requests':goals,
            'scope_source':req.scope_source.model_copy(update={'message_id':'m2'})})
        done=workflow.advance(session.id,text)
        self.assertEqual(done.phase,Phase.EXECUTING)
        self.assertEqual(done.requirement_version,2)
        self.assertEqual(done.usage.experiments,1)

    def test_plain_priority_quote_does_not_grant_high_priority(self):
        req,text=demand_set()
        wrong=req.requests[0].model_copy(update={'priority':'high'})
        _,transport,session=self.start(req.model_copy(update={'requests':(wrong,req.requests[1])}),text)
        self.assertEqual(session.phase,Phase.WAITING)
        self.assertEqual(transport.calls,[])

    def test_other_users_targets_and_broad_quotes_cannot_be_rebound(self):
        req,text=demand_set('ue1')
        first,second=req.requests
        # Keep valid UE sources but swap all three per-user targets and their genuine quotes.
        keys=('min_bandwidth_mbps','max_bandwidth_mbps','priority')
        changed=[]
        for own,other in ((first,second),(second,first)):
            sources=dict(own.sources,**{key:other.sources[key] for key in keys})
            changed.append(own.model_copy(update={**{key:getattr(other,key) for key in keys},'sources':sources}))
        _,transport,session=self.start(req.model_copy(update={'requests':tuple(changed)}),text)
        self.assertEqual(session.phase,Phase.WAITING)
        self.assertEqual(transport.calls,[])
        sources=dict(first.sources,min_bandwidth_mbps=Source(message_id='m1',quote=text))
        _,transport,session=self.start(req.model_copy(update={'requests':(first.model_copy(update={'sources':sources}),second)}),text)
        self.assertEqual(session.phase,Phase.WAITING)
        self.assertEqual(transport.calls,[])

    def test_experiment_duration_and_measurement_window_cannot_exchange_roles(self):
        req,text=demand_set()
        text=text.replace('实验30秒，测量窗口20秒','实验20秒，测量窗口30秒')
        goals=[]
        for goal in req.requests:
            sources=dict(goal.sources,valid_for_seconds=Source(message_id='m1',quote='测量窗口30秒'),
                         measurement_window_seconds=Source(message_id='m1',quote='实验20秒'))
            goals.append(goal.model_copy(update={'sources':sources}))
        _,transport,session=self.start(req.model_copy(update={'requests':tuple(goals)}),text)
        self.assertEqual(session.phase,Phase.WAITING)
        self.assertEqual(transport.calls,[])

    def test_other_users_protocol_and_direction_cannot_supply_common_settings(self):
        req,text=demand_set()
        for key,wrong_text,wrong_quote in [('protocol','UE1 TCP下行；UE2 UDP下行。','UDP'),
                ('direction','UE1 UDP上行；UE2 UDP下行。','下行'),
                ('valid_for_seconds','UE1 实验20秒；UE2 实验30秒。','实验30秒'),
                ('measurement_window_seconds','UE1 测量窗口10秒；UE2 测量窗口20秒。','测量窗口20秒')]:
            prefix='C1受控下行竞争实验；双方UDP下行，实验30秒，测量窗口20秒。'
            message=prefix+wrong_text+text.split('。',1)[1]
            # The extraction falsely reuses UE2's supported protocol/direction for UE1.
            goals=[]
            for goal in req.requests:
                sources=dict(goal.sources,**{key:Source(message_id='m1',quote=wrong_quote)})
                goals.append(goal.model_copy(update={'sources':sources}))
            _,transport,session=self.start(req.model_copy(update={'requests':tuple(goals)}),message)
            self.assertEqual(session.phase,Phase.WAITING,key)
            self.assertEqual(transport.calls,[])

    def test_live_cli_start_then_resume_needs_no_model_or_key(self):
        from services.cnf_agent.cli import main
        transport=Transport()
        req,text=demand_set()
        model=Model(req);model.close=MagicMock()
        db=Path(self.temp.name)/'cli.sqlite'
        base=['--db',str(db),'live']
        factory=lambda memory,provider,tools: LiveWorkflow(memory,provider,tools,now=lambda:AT)
        with patch('services.cnf_agent.deepseek.DeepSeekSettings.load',return_value=object()), \
             patch('services.cnf_agent.deepseek.DeepSeekProvider',return_value=model), \
             patch('services.cnf_agent.live_transport.SshTransport',return_value=transport), \
             patch('services.cnf_agent.live_workflow.LiveWorkflow',side_effect=factory),redirect_stdout(io.StringIO()):
            self.assertEqual(main(base+['--message',text]),0)
        with Memory(db) as memory:
            session_id=memory.db.execute('SELECT id FROM sessions').fetchone()['id']
        transport.running=False
        with patch('services.cnf_agent.deepseek.DeepSeekSettings.load',side_effect=AssertionError('No key needed')), \
             patch('services.cnf_agent.live_transport.SshTransport',return_value=transport), \
             patch('services.cnf_agent.live_workflow.LiveWorkflow',side_effect=factory),redirect_stdout(io.StringIO()):
            self.assertEqual(main(base+['--session',session_id,'--wait']),0)
        self.assertEqual(transport.calls.count('execute'),1)
        model.close.assert_called_once()

    def test_live_cli_rejects_replay_session_before_model_or_transport(self):
        from services.cnf_agent.cli import main
        from services.cnf_agent.schemas import Session
        self.memory.create(Session(id='replay'))
        with patch('services.cnf_agent.deepseek.DeepSeekSettings.load') as settings,redirect_stderr(io.StringIO()):
            code=main(['--db',str(Path(self.temp.name)/'agent.sqlite'),'live','--session','replay'])
        self.assertEqual(code,2)
        settings.assert_not_called()

    def test_worker_claim_deduplicates_even_if_launch_response_is_lost(self):
        operation_id='a'*32
        payload={'state':{},'batch':{},'plan':{'policyPlanHash':'plan'}}
        with patch.object(live_worker,'OPERATIONS',Path(self.temp.name)/'ops'), \
             patch.object(live_worker.subprocess,'Popen',return_value=SimpleNamespace(pid=123)) as launch, \
             patch.object(live_worker,'process_stamp',return_value='start'):
            first=live_worker.start(operation_id,payload)
            second=live_worker.start(operation_id,payload)
            self.assertEqual(first,second)
            self.assertEqual(launch.call_count,1)
            with self.assertRaises(ValueError):live_worker.start(operation_id,dict(payload,batch={'different':True}))

    def test_unknown_or_dead_worker_is_never_restarted_by_query(self):
        with patch.object(live_worker,'OPERATIONS',Path(self.temp.name)/'ops'), \
             patch.object(live_worker.subprocess,'Popen') as launch:
            self.assertEqual(live_worker.query('b'*32)['status'],'unknown')
            launch.assert_not_called()
            with self.assertRaises(ValueError):live_worker.query('../escape')

    def test_partial_claim_and_duplicate_run_marker_never_launch_again(self):
        folder=Path(self.temp.name)/'ops'/('c'*32)
        folder.mkdir(parents=True)
        payload={'state':{},'batch':{},'plan':{'policyPlanHash':'plan'}}
        with patch.object(live_worker,'OPERATIONS',folder.parent),patch.object(live_worker.subprocess,'Popen') as launch:
            with self.assertRaises(ValueError):live_worker.start(folder.name,payload)
            live_worker.write(folder/'receipt.json',{'payload':payload,'payload_hash':live_worker.digest(payload)})
            (folder/'started').touch()
            with self.assertRaises(FileExistsError):live_worker.run_operation(folder.name)
            launch.assert_not_called()

    def test_worker_preserves_recovered_failure_and_writes_queryable_result(self):
        folder=Path(self.temp.name)/'ops'/('d'*32)
        folder.mkdir(parents=True)
        payload={'state':{},'batch':{},'plan':{'policyPlanHash':'plan'}}
        live_worker.write(folder/'receipt.json',{'payload':payload,'payload_hash':live_worker.digest(payload)})
        def fail_after_recovery(path,*args):
            path.mkdir()
            live_worker.write(path/'summary.json',dict(outcome='failed',restoration='restored',finalStrictBaselinePassed=True))
            raise RuntimeError('failed after recovery')
        with patch.object(live_worker,'OPERATIONS',folder.parent), \
             patch.object(live_worker,'policy_module',return_value=SimpleNamespace(run=fail_after_recovery)):
            live_worker.run_operation(folder.name)
            result=live_worker.query(folder.name)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['result']['summary']['outcome'],'failed')
        self.assertEqual(result['result']['summary']['restoration'],'restored')
        self.assertEqual(result['result']['error_code'],'RuntimeError')

    def test_transport_uses_only_fixed_host_and_json_stdin(self):
        run=SimpleNamespace(returncode=0,stdout='{"ok":true,"value":{"status":"unknown"}}')
        transport=SshTransport('a'*64)
        with patch('services.cnf_agent.live_transport.subprocess.run',return_value=run) as call:
            transport.call('result',{'operation_id':'b'*32})
        args=call.call_args.args[0]
        self.assertIn('free5gc-vm-1',args)
        self.assertNotIn('b'*32,' '.join(args))
        self.assertEqual(json.loads(call.call_args.kwargs['input'])['arguments']['operation_id'],'b'*32)
        with self.assertRaises(ValueError):transport.call('shell',{})

    def test_bundle_is_deterministic_and_excludes_credentials_and_local_state(self):
        first,raw=bundle()
        second,_=bundle()
        self.assertEqual(first,second)
        import io,zipfile
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names=archive.namelist()
        self.assertIn('services/cnf_agent/live_worker.py',names)
        self.assertFalse(any('.work' in name or 'deepseek.local.json' in name for name in names))


if __name__ == '__main__':
    unittest.main()
