"""F2 contract, provider and readonly gates; no paid model calls in unit tests."""
from datetime import datetime, timedelta, timezone
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from services.cnf_agent.deepseek import DeepSeekProvider, DeepSeekSettings
from services.cnf_agent.memory import Memory
from services.cnf_agent.provider import ProviderCallError, ReplayProvider
from services.cnf_agent.readonly import DashboardReader, FixtureReader, ReadOnlyWorkflow, numeric_sources_valid
from services.cnf_agent.schemas import (
    Budget, Extraction, Interpretation, InterpretationReply, Message, ModelReply,
    Phase, ReadFacts, ReadRequest, Requirement, Source,
)
from services.cnf_agent.trace import summary


ROOT = Path(__file__).resolve().parents[1]
SECRET = 'test-secret-not-for-logging'
TEXT = 'UE1 视频业务访问边缘服务，下载上限 5 Mbps。'


def requirement():
    values = dict(user_id='ue1', service_id='video', direction='downlink',
                  destination='edge', max_bandwidth_mbps=5.0)
    quotes = dict(user_id='UE1', service_id='视频业务', direction='下载',
                  destination='边缘服务', max_bandwidth_mbps='上限 5 Mbps')
    return Requirement(**values, sources={key: Source(message_id='m1', quote=quotes[key]) for key in values})


def response(content=None, tool=False, finish='stop', tokens=100):
    call = SimpleNamespace(id='call-read', function=SimpleNamespace(
        name='inspect_network', arguments='{"scope":"overview"}'))
    message = SimpleNamespace(content=content, tool_calls=[call] if tool else [],
                              reasoning_content='PRIVATE_REASONING_NOT_FOR_LOGS')
    message.model_dump = lambda **kwargs: dict(role='assistant', content=content,
        reasoning_content=message.reasoning_content,
        tool_calls=[{'id':call.id,'type':'function','function':{'name':call.function.name,
                                                             'arguments':call.function.arguments}}] if tool else [])
    usage = SimpleNamespace(total_tokens=tokens, prompt_tokens=60, completion_tokens=tokens-60,
                            completion_tokens_details=SimpleNamespace(reasoning_tokens=10))
    return SimpleNamespace(id='response-test', model='deepseek-flash', usage=usage,
                           choices=[SimpleNamespace(message=message, finish_reason=finish)])


def provider(responses):
    client = MagicMock()
    client.chat.completions.create.side_effect = responses
    config = DeepSeekSettings(api_key=SECRET)
    return DeepSeekProvider(config, client), client


class FakeModel:
    def extract(self, messages, max_tokens):
        return ModelReply(extraction=Extraction(requirement=requirement()), tokens=10)

    def request_read(self, req, max_tokens):
        return ReadRequest(tool_call_id='read', tokens=10)

    def explain(self, call, facts, max_tokens):
        return InterpretationReply(tokens=10, interpretation=Interpretation(
            summary='只读观测，未执行配置', limitations=('不能证明业务目标成功',)))


class F2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = Memory(Path(self.temp.name)/'memory.sqlite')
        self.addCleanup(self.memory.close)
        self.reader = FixtureReader(ROOT/'tests/fixtures/cnf-agent-f2-facts.json')

    def workflow(self, model=None, reader=None):
        return ReadOnlyWorkflow(self.memory, model or FakeModel(), reader or self.reader)

    def test_real_model_mode_never_creates_plan_operation_or_experiment(self):
        workflow = self.workflow()
        session = workflow.advance(workflow.create().id, TEXT)
        self.assertEqual(session.phase, Phase.READ_ONLY_COMPLETE)
        self.assertEqual(session.mode, 'real_model_readonly')
        self.assertEqual(session.usage.experiments, 0)
        self.assertIsNone(session.operation_id)
        self.assertIsNone(session.plan)
        self.assertFalse(session.interpretation.goal_verified)
        self.assertEqual(session.read_facts.mode, 'read_only_fixture')
        self.assertTrue(all(event['mode']=='real_model_readonly' for event in self.memory.events(session.id)))
        self.assertIn('F2 真实模型，只读', summary(session))

    def test_missing_request_stops_before_model_tools(self):
        class Missing(FakeModel):
            def extract(self, messages, max_tokens):
                return ModelReply(extraction=Extraction(requirement=Requirement()), tokens=10)

        model = Missing()
        model.request_read = MagicMock(side_effect=AssertionError('Should not query'))
        workflow = self.workflow(model)
        result = workflow.advance(workflow.create().id, '改善视频业务')
        self.assertEqual(result.phase, Phase.WAITING)
        model.request_read.assert_not_called()

    def test_persisted_modification_clears_old_observation_when_new_goal_is_incomplete(self):
        workflow = self.workflow()
        session = workflow.advance(workflow.create().id, TEXT)
        class Missing(FakeModel):
            def extract(self, messages, max_tokens):
                return ModelReply(extraction=Extraction(requirement=Requirement()), tokens=10)
        workflow = self.workflow(Missing())
        changed = workflow.advance(session.id, '取消带宽上限')
        self.assertEqual(changed.phase, Phase.WAITING)
        self.assertIsNone(changed.read_facts)
        self.assertIsNone(changed.interpretation)
        self.assertEqual(changed.requirement_version, 2)

    def test_f2_rejects_writing_budget_and_replay_session(self):
        workflow = self.workflow()
        with self.assertRaises(ValueError):
            workflow.create(Budget(experiments=1))
        from services.cnf_agent.tools import SimulatedTools
        from services.cnf_agent.workflow import Workflow
        replay = Workflow(self.memory, ReplayProvider(()), SimulatedTools()).create()
        result = workflow.advance(replay.id, TEXT)
        self.assertEqual(result.phase, Phase.FAILED)
        self.assertEqual(result.usage.model_calls, 0)

    def test_budget_stops_before_read_and_accounts_failed_model_response_tokens(self):
        class Failed(FakeModel):
            def extract(self, messages, max_tokens):
                raise ProviderCallError('invalid_structured_output', tokens=123, metadata={'response_id':'test'})
        workflow = self.workflow(Failed())
        result = workflow.advance(workflow.create().id, TEXT)
        self.assertEqual(result.usage.tokens, 123)
        self.assertEqual(result.phase, Phase.FAILED)
        self.assertIn('invalid_structured_output', result.notes[0])
        workflow = self.workflow()
        result = workflow.advance(workflow.create(Budget(model_calls=1, experiments=0)).id, TEXT)
        self.assertEqual(result.phase, Phase.BUDGET)
        self.assertEqual(result.usage.queries, 0)

    def test_deepseek_settings_hide_key_and_refuse_other_destinations(self):
        config = DeepSeekSettings(api_key=SECRET)
        self.assertNotIn(SECRET, repr(config))
        with self.assertRaises(ValidationError):
            DeepSeekSettings(api_key=SECRET, base_url='https://other.example')
        path = Path(self.temp.name)/'bad.json'
        path.write_text(json.dumps({'api_key':SECRET,'base_url':'http://api.deepseek.com'}),encoding='utf-8')
        with self.assertRaises(ValueError) as failure:
            DeepSeekSettings.load(path)
        self.assertNotIn(SECRET, str(failure.exception))
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY':SECRET}):
            self.assertEqual(DeepSeekSettings.load().api_key.get_secret_value(), SECRET)

    def test_provider_uses_exact_model_thinking_json_and_real_usage(self):
        output = Extraction(requirement=requirement()).model_dump_json()
        model, client = provider([response(output)])
        reply = model.extract((Message(id='m1',text=TEXT),),65536)
        args = client.chat.completions.create.call_args.kwargs
        self.assertEqual(args['model'],'deepseek-flash')
        self.assertEqual(args['extra_body'],{'thinking':{'type':'enabled'}})
        self.assertEqual(args['reasoning_effort'],'high')
        self.assertEqual(args['response_format'],{'type':'json_object'})
        self.assertEqual(reply.tokens,100)
        self.assertNotIn(SECRET, reply.model_dump_json())
        self.assertNotIn('PRIVATE_REASONING', reply.model_dump_json())

    def test_sdk_errors_and_invalid_output_do_not_leak_key_or_lose_billed_usage(self):
        for result in (response('{bad'), response('',finish='length')):
            model, _ = provider([result])
            with self.assertRaises(ProviderCallError) as failed:
                model.extract((Message(id='m1',text=TEXT),),65536)
            self.assertEqual(failed.exception.tokens,100)
            self.assertNotIn(SECRET,str(failed.exception))
        model, _ = provider([RuntimeError(SECRET)])
        with self.assertRaises(ProviderCallError) as failed:
            model.extract((Message(id='m1',text=TEXT),),65536)
        self.assertEqual(str(failed.exception),'deepseek_transport_error')

    def test_model_has_only_one_readonly_tool_and_reasoning_is_only_ephemeral(self):
        answer = Interpretation(summary='只读', limitations=('未验证目标',)).model_dump_json()
        model, client = provider([response(tool=True,finish='tool_calls'), response(answer)])
        call = model.request_read(requirement(),65536)
        reply = model.explain(call,self.reader.facts,65536)
        calls = client.chat.completions.create.call_args_list
        self.assertEqual([t['function']['name'] for t in calls[0].kwargs['tools']],['inspect_network'])
        context = calls[1].kwargs['messages']
        self.assertEqual(calls[1].kwargs['tool_choice'],'none')
        self.assertEqual(calls[1].kwargs['response_format'],{'type':'json_object'})
        self.assertTrue(any('reasoning_content' in message for message in context))
        self.assertNotIn('PRIVATE_REASONING',reply.model_dump_json())
        self.assertEqual(model._read_contexts,{})

    def test_unapproved_tool_or_arguments_are_rejected_without_invocation(self):
        for changes in (dict(name='execute_experiment'),dict(arguments='{"scope":"overview","command":"delete"}')):
            reply = response(tool=True,finish='tool_calls')
            for key,value in changes.items():
                setattr(reply.choices[0].message.tool_calls[0].function,key,value)
            model, _ = provider([reply])
            with self.assertRaises(ProviderCallError) as failed:
                model.request_read(requirement(),65536)
            self.assertEqual(str(failed.exception),'invalid_read_tool_call')

    def test_new_tool_call_or_claim_of_execution_in_explanation_is_rejected(self):
        for final in (response(tool=True,finish='tool_calls'),response('{"summary":"done","limitations":["x"],"network_config_changed":true}')):
            model,_=provider([response(tool=True,finish='tool_calls'), final])
            call=model.request_read(requirement(),65536)
            with self.assertRaises(ProviderCallError):
                model.explain(call,self.reader.facts,65536)

    def test_numeric_sources_check_units_and_reject_invented_values(self):
        req = requirement()
        self.assertTrue(numeric_sources_valid(req))
        for quote,value in [('上限 5000 Kbps',5.0),('上限 1 MB/s',8.0)]:
            sources=dict(req.sources,max_bandwidth_mbps=Source(message_id='m1',quote=quote))
            changed=req.model_copy(update={'max_bandwidth_mbps':value,'sources':sources})
            self.assertTrue(numeric_sources_valid(changed))
        self.assertFalse(numeric_sources_valid(req.model_copy(update={'max_bandwidth_mbps':8.0})))

    def test_dashboard_reader_only_returns_fresh_aggregate_data_and_rejects_cache_errors(self):
        at=datetime.now(timezone.utc)
        state={'source':'ssh','error':None,'data':{'observedAt':at.isoformat(),
            'nodes':[{'ready':True,'ip':'PRIVATE_IP'}], 'pods':[{'ready':True}], 'routes':[{}]}}
        opener=MagicMock()
        handle=MagicMock()
        opener.open.return_value.__enter__.return_value=handle
        reader=DashboardReader(now=lambda:at)
        for source,error,age,mode in [('ssh',None,0,'read_only_observed'),('replay',None,0,'read_only_fixture'),
                                      ('ssh','timeout',0,'unavailable'),('ssh',None,121,'unavailable')]:
            state['source'],state['error']=source,error
            state['data']['observedAt']=(at-timedelta(seconds=age)).isoformat()
            handle.read.return_value=json.dumps(state).encode()
            with patch('services.cnf_agent.readonly.build_opener',return_value=opener):
                facts=reader.inspect_network('overview')
            self.assertEqual(facts.mode,mode)
            self.assertNotIn('PRIVATE_IP',facts.model_dump_json())
        with self.assertRaises(ValueError):
            reader.inspect_network('arbitrary-url')

    def test_fixture_cannot_claim_to_be_live(self):
        path=Path(self.temp.name)/'facts.json'
        path.write_text(self.reader.facts.model_copy(update={'mode':'read_only_observed'}).model_dump_json(),encoding='utf-8')
        with self.assertRaises(ValueError):
            FixtureReader(path)

    def test_chat_cli_preserves_readonly_mode_and_continues_an_existing_session(self):
        from services.cnf_agent.cli import main

        config = Path(self.temp.name)/'provider.json'
        config.write_text(json.dumps({'api_key':SECRET}),encoding='utf-8')
        model = FakeModel()
        model.close = MagicMock()
        base = ['--db',str(Path(self.temp.name)/'cli.sqlite'),'chat','--config',str(config),
                '--facts-file',str(ROOT/'tests/fixtures/cnf-agent-f2-facts.json'),'--message',TEXT]
        with patch('services.cnf_agent.deepseek.DeepSeekProvider',return_value=model), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(base),0)
            with Memory(Path(self.temp.name)/'cli.sqlite') as memory:
                session_id = memory.db.execute('SELECT id FROM sessions').fetchone()['id']
            self.assertEqual(main(base+['--session',session_id]),0)
        self.assertNotIn(SECRET,output.getvalue())
        self.assertEqual(model.close.call_count,2)

    def test_invalid_numeric_citation_stops_before_any_tool_request(self):
        class WrongNumber(FakeModel):
            def extract(self,messages,max_tokens):
                req=requirement().model_copy(update={'max_bandwidth_mbps':8.0})
                return ModelReply(extraction=Extraction(requirement=req),tokens=10)
        model=WrongNumber()
        model.request_read=MagicMock(side_effect=AssertionError('Should not be reached'))
        workflow=self.workflow(model)
        session=workflow.advance(workflow.create().id,TEXT)
        self.assertEqual(session.phase,Phase.WAITING)
        model.request_read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
