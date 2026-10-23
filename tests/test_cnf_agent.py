"""F1 acceptance: orchestration behavior and persistence, without any network."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError

from services.cnf_agent.memory import ConcurrentUpdate, Memory
from services.cnf_agent.provider import ReplayFrame, ReplayProvider
from services.cnf_agent.schemas import (
    Budget, Extraction, OperationResult, Phase, Plan, Requirement, Session, Source,
)
from services.cnf_agent.tools import SimulatedTools
from services.cnf_agent.trace import export_events, summary
from services.cnf_agent.workflow import Workflow


ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 10, 23, 10, tzinfo=timezone.utc)
GOALS = dict(user_id='ue1', service_id='video', direction='downlink',
             destination='edge', max_bandwidth_mbps=5.0)


def frame(goals=None, message_id='m1', message=None, ambiguities=()):
    goals = GOALS if goals is None else goals
    text = message or ' '.join(f'{key}={value}' for key, value in goals.items())
    requirement = Requirement(**goals, sources={
        key: Source(message_id=message_id, quote=str(value)) for key, value in goals.items()})
    return ReplayFrame(message=text, extraction=Extraction(requirement=requirement, ambiguities=ambiguities))


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'memory.sqlite'
        self.memory = Memory(self.path)
        self.addCleanup(self.memory.close)

    def workflow(self, frames=None, tools=None, **kwargs):
        return Workflow(self.memory, ReplayProvider(frames or (frame(),)),
                        tools or SimulatedTools(), now=lambda: AT, **kwargs)

    def run_frame(self, recorded=None, tools=None, budget=None):
        recorded = recorded or frame()
        tools = tools or SimulatedTools()
        workflow = self.workflow((recorded,), tools)
        session = workflow.create(budget)
        return workflow, tools, workflow.advance(session.id, recorded.message)

    def test_complete_run_records_goals_observation_plan_checks_and_restoration(self):
        workflow, tools, result = self.run_frame()
        self.assertEqual(result.phase, Phase.SUCCEEDED)
        self.assertEqual(result.result.restoration, 'restored')
        self.assertEqual([a.kind for a in result.plan.actions], ['bind_service', 'limit_bandwidth'])
        self.assertEqual(result.plan.actions[0].destination, 'edge')
        self.assertEqual(result.plan.actions[1].max_mbps, 5)
        self.assertEqual(result.plan.session_id, 'sim-session-ue1')
        self.assertEqual((result.usage.model_calls, result.usage.queries, result.usage.experiments), (1, 2, 1))
        events = self.memory.events(result.id)
        kinds = [e['kind'] for e in events]
        self.assertLess(kinds.index('execute_experiment_started'), kinds.index('execute_experiment_returned'))
        self.assertTrue(all(event['mode'] == 'simulated' for event in events))
        self.assertEqual(workflow.advance(result.id).revision, result.revision)
        self.assertEqual(tools.execution_count, 1)

    def test_goal_changes_produce_different_simulated_actions_without_experiment_ids(self):
        _, _, original = self.run_frame()
        _, _, changed = self.run_frame(frame(dict(GOALS, destination='core', max_bandwidth_mbps=3.0)))
        self.assertNotEqual(original.plan.requirement_ref, changed.plan.requirement_ref)
        self.assertEqual(changed.plan.actions[0].destination, 'core')
        self.assertEqual(changed.plan.actions[1].max_mbps, 3)
        self.assertNotIn('equal', changed.plan.model_dump_json())

    def test_missing_information_waits_without_inspection_or_execution(self):
        _, tools, result = self.run_frame(frame({'user_id': 'ue1'}))
        self.assertEqual(result.phase, Phase.WAITING)
        self.assertEqual(result.usage.queries, 0)
        self.assertEqual(tools.execution_count, 0)
        self.assertIsNone(result.requirement.max_bandwidth_mbps)

    def test_clarification_survives_database_reopen_and_preserves_versions_and_sources(self):
        first = frame({'user_id': 'ue1'})
        second = frame(message_id='m2')
        workflow = self.workflow((first, second))
        session = workflow.create()
        waiting = workflow.advance(session.id, first.message)
        with Memory(self.path) as reopened:
            workflow = Workflow(reopened, ReplayProvider((first, second)), SimulatedTools(), now=lambda: AT)
            result = workflow.advance(session.id, second.message)
            self.assertEqual(result.phase, Phase.SUCCEEDED)
            self.assertEqual(result.requirement_version, 2)
            self.assertEqual(result.messages[0].text, waiting.messages[0].text)
            history = [e for e in reopened.events(session.id) if e['kind'] == 'requirement_extracted']
            self.assertEqual([e['payload']['requirement_version'] for e in history], [1, 2])

    def test_user_amendment_can_reference_previous_turn(self):
        first = frame({'user_id': 'ue1'})
        complete = frame(message_id='m2')
        sources = dict(complete.extraction.requirement.sources)
        sources['user_id'] = Source(message_id='m1', quote='ue1')
        requirement = complete.extraction.requirement.model_copy(update={'sources': sources})
        second = complete.model_copy(update={'extraction': Extraction(requirement=requirement)})
        workflow = self.workflow((first, second))
        session = workflow.create()
        workflow.advance(session.id, first.message)
        result = workflow.advance(session.id, second.message)
        self.assertEqual(result.phase, Phase.SUCCEEDED)
        self.assertEqual(result.requirement.sources['user_id'].message_id, 'm1')

    def test_conflicts_and_ambiguity_wait_before_tool_calls(self):
        cases = [frame(dict(GOALS, min_bandwidth_mbps=6.0)), frame(ambiguities=('哪个用户？',))]
        for recorded in cases:
            with self.subTest(message=recorded.message):
                _, tools, result = self.run_frame(recorded)
                self.assertEqual(result.phase, Phase.WAITING)
                self.assertEqual(result.usage.queries, 0)
                self.assertEqual(tools.execution_count, 0)

    def test_over_capacity_unsupported_sla_direction_and_unknown_user_are_rejected(self):
        for changes in (dict(max_bandwidth_mbps=11.0), dict(min_bandwidth_mbps=2.0),
                        dict(max_latency_ms=20.0), dict(max_loss_percent=0.1),
                        dict(direction='uplink'), dict(user_id='ue9')):
            with self.subTest(changes=changes):
                _, tools, result = self.run_frame(frame(dict(GOALS, **changes)))
                self.assertEqual(result.phase, Phase.REJECTED)
                self.assertEqual(tools.execution_count, 0)

    def test_false_source_reference_is_rejected_before_querying(self):
        recorded = frame(message_id='missing-message')
        _, tools, result = self.run_frame(recorded)
        self.assertEqual(result.phase, Phase.FAILED)
        self.assertEqual(tools.execution_count, 0)
        self.assertEqual(result.usage.queries, 0)

    def test_model_error_and_unknown_replay_input_never_reach_tools(self):
        workflow = self.workflow()
        result = workflow.advance(workflow.create().id, 'unrecorded input')
        self.assertEqual(result.phase, Phase.FAILED)
        self.assertEqual(result.usage.model_calls, 1)
        self.assertEqual(result.usage.queries, 0)
        self.assertNotIn('unrecorded input', result.notes[0])

    def test_tool_failures_have_distinct_outcomes(self):
        for fault, phase in [('inspect_error', Phase.FAILED), ('execute_error', Phase.FAILED),
                             ('target_unmet', Phase.FAILED), ('restore_failed', Phase.RESTORE_FAILED),
                             ('result_unknown', Phase.UNKNOWN), ('response_lost', Phase.SUCCEEDED)]:
            with self.subTest(fault=fault):
                workflow, tools, result = self.run_frame(tools=SimulatedTools(fault))
                self.assertEqual(result.phase, phase)
                if fault in ('restore_failed', 'result_unknown'):
                    with self.assertRaises(ValueError):
                        workflow.advance(result.id, 'new request')
                if fault == 'response_lost':
                    self.assertEqual(tools.execution_count, 1)
                    self.assertEqual(result.usage.queries, 3)

    def test_uncertain_result_only_queries_on_retry_even_with_new_adapter_instance(self):
        _, tools, result = self.run_frame(tools=SimulatedTools('result_unknown'))
        new_tools = SimulatedTools()
        result = self.workflow(tools=new_tools).advance(result.id)
        self.assertEqual(result.phase, Phase.UNKNOWN)
        self.assertEqual(new_tools.execution_count, 0)
        self.assertEqual(tools.execution_count, 1)
        self.assertIsNotNone(result.operation_id)

    def test_process_interruption_after_claim_does_not_reexecute(self):
        class Interrupted(SimulatedTools):
            def execute_experiment(self, operation_id, plan):
                raise KeyboardInterrupt()

        workflow = self.workflow(tools=Interrupted())
        session = workflow.create()
        with self.assertRaises(KeyboardInterrupt):
            workflow.advance(session.id, frame().message)
        stored = self.memory.load(session.id)
        self.assertEqual(stored.phase, Phase.EXECUTING)
        new_tools = SimulatedTools()
        recovered = self.workflow(tools=new_tools).advance(session.id)
        self.assertEqual(recovered.phase, Phase.UNKNOWN)
        self.assertEqual(new_tools.execution_count, 0)

    def test_model_query_step_token_and_experiment_budgets_stop_writes(self):
        budgets = [Budget(queries=1), Budget(steps=2), Budget(tokens=64), Budget(experiments=0)]
        for budget in budgets:
            with self.subTest(budget=budget):
                _, tools, result = self.run_frame(budget=budget)
                self.assertEqual(result.phase, Phase.BUDGET)
                self.assertEqual(tools.execution_count, 0)
        first, second = frame({'user_id': 'ue1'}), frame(message_id='m2')
        workflow = self.workflow((first, second))
        session = workflow.create(Budget(model_calls=1))
        workflow.advance(session.id, first.message)
        result = workflow.advance(session.id, second.message)
        self.assertEqual(result.phase, Phase.BUDGET)
        self.assertEqual(result.usage.model_calls, 1)

    def test_elapsed_budget_is_cumulative_over_calls(self):
        clock = iter([0, 2, 2, 4])
        workflow = self.workflow(monotonic=lambda: next(clock))
        result = workflow.advance(workflow.create(Budget(elapsed_seconds=3)).id, frame().message)
        self.assertEqual(result.phase, Phase.BUDGET)
        self.assertEqual(result.usage.elapsed_seconds, 4)
        self.assertEqual(result.usage.experiments, 0)

    def test_expired_observation_and_forged_plan_are_rejected(self):
        class Expired(SimulatedTools):
            def inspect_network(self, at):
                return super().inspect_network(at - timedelta(seconds=121))

        class Forged(SimulatedTools):
            def preview_policy(self, *args):
                preview = super().preview_policy(*args)
                return preview.model_copy(update={'plan': preview.plan.model_copy(update={'session_id': 'wrong'})})

        class WrongActions(SimulatedTools):
            def preview_policy(self, *args):
                preview = super().preview_policy(*args)
                action = preview.plan.actions[1].model_copy(update={'max_mbps': 9.0})
                plan = preview.plan.model_copy(update={'actions': (preview.plan.actions[0], action)})
                return preview.model_copy(update={'plan': plan})

        for tools in (Expired(), Forged(), WrongActions()):
            _, _, result = self.run_frame(tools=tools)
            self.assertEqual(result.phase, Phase.REJECTED)
            self.assertEqual(tools.execution_count, 0)

    def test_wrong_operation_result_cannot_become_success(self):
        class WrongResult(SimulatedTools):
            def execute_experiment(self, *args):
                return super().execute_experiment(*args).model_copy(update={'operation_id': 'other'})

        _, _, result = self.run_frame(tools=WrongResult())
        self.assertEqual(result.phase, Phase.UNKNOWN)
        self.assertIsNone(result.result)

    def test_plan_version_capability_observation_and_expiry_are_bound(self):
        for changes in ({'requirement_version': 9}, {'observation_ref': 'other'},
                        {'requirement_ref': 'other'}, {'adapter_version': 'live-adapter'},
                        {'expires_at': AT - timedelta(seconds=1)}):
            class Altered(SimulatedTools):
                def preview_policy(self, *args):
                    preview = super().preview_policy(*args)
                    return preview.model_copy(update={'plan': preview.plan.model_copy(update=changes)})

            with self.subTest(changes=changes):
                tools = Altered()
                _, _, result = self.run_frame(tools=tools)
                self.assertEqual(result.phase, Phase.REJECTED)
                self.assertEqual(tools.execution_count, 0)

    def test_execution_claim_is_unique_and_failed_claim_rolls_back_snapshot_and_event(self):
        session = self.workflow().create()
        prepared = session.model_copy(update={'requirement_version': 1, 'operation_id': 'op1'})
        saved = self.memory.save(prepared, 'claim', claim_operation=True)
        with self.assertRaises(sqlite3.IntegrityError):
            self.memory.save(saved.model_copy(update={'operation_id': 'op2'}), 'duplicate', claim_operation=True)
        self.assertEqual(self.memory.load(session.id).operation_id, 'op1')
        self.assertEqual(len(self.memory.events(session.id)), 2)

    def test_session_isolation_and_optimistic_concurrency(self):
        workflow = self.workflow()
        one, two = workflow.create(), workflow.create()
        self.run_frame()
        self.assertEqual(self.memory.load(two.id).messages, ())
        stale = self.memory.load(one.id)
        self.memory.save(stale, 'first')
        with Memory(self.path) as other:
            with self.assertRaises(ConcurrentUpdate):
                other.save(stale, 'stale')
        self.assertEqual([event['kind'] for event in self.memory.events(one.id)], ['session_created', 'first'])

    def test_schema_rejects_unknown_actions_unknown_fields_unproven_success_and_nan(self):
        with self.assertRaises(ValidationError):
            Requirement(user_id='ue1')
        with self.assertRaises(ValidationError):
            Requirement.model_validate_json('{"max_bandwidth_mbps": NaN, "sources": {}}')
        with self.assertRaises(ValidationError):
            Session(id='one', mode='live')
        with self.assertRaises(ValidationError):
            Budget(experiments=True)
        with self.assertRaises(ValidationError):
            Requirement(unexpected='value')
        _, _, completed = self.run_frame()
        invalid = completed.plan.model_dump(mode='json')
        invalid['actions'][0]['kind'] = 'shell'
        with self.assertRaises(ValidationError):
            Plan.model_validate_json(json.dumps(invalid))
        with self.assertRaises(ValidationError):
            OperationResult(operation_id='one', plan_ref='plan', outcome='succeeded',
                            restoration='restored', detail='no checks')
        self.assertIn('cnf-agent-session-v1', json.dumps(Session.model_json_schema()))

    def test_trace_export_contains_inputs_results_and_clear_simulation_label(self):
        _, _, result = self.run_frame()
        output = Path(self.temp.name) / 'events.jsonl'
        export_events(self.memory.events(result.id), output)
        events = [json.loads(line) for line in output.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(events[-1]['payload']['result']['restoration'], 'restored')
        self.assertEqual(events[1]['payload']['messages'][0]['text'], frame().message)
        self.assertIn('无真实模型/网络操作', summary(result))
        with self.assertRaises(FileExistsError):
            export_events(events, output)

    def test_frozen_development_replays_match_expected_results(self):
        from services.cnf_agent.cli import ReplayFile

        replay = ReplayFile.model_validate_json((ROOT / 'tests/fixtures/cnf-agent-replay.json').read_text(encoding='utf-8'))
        self.assertEqual(len(replay.scenarios), 11)
        for name, scenario in replay.scenarios.items():
            with self.subTest(scenario=name):
                workflow = self.workflow(scenario.frames, SimulatedTools(scenario.fault))
                session = workflow.create()
                for recorded in scenario.frames:
                    session = workflow.advance(session.id, recorded.message)
                    if session.phase != Phase.WAITING:
                        break
                self.assertEqual(session.phase, scenario.expected_phase)

    def test_cli_can_run_continue_show_export_and_replay_in_separate_processes(self):
        replay = Path(self.temp.name) / 'replay.json'
        first, second = frame({'user_id': 'ue1'}), frame(message_id='m2')
        replay.write_text(json.dumps({'version': 'cnf-agent-replay-v1', 'mode': 'simulated', 'scenarios': {
            'missing': {'frames': [f.model_dump(mode='json') for f in (first, second)],
                        'expected_phase': 'succeeded'}}}), encoding='utf-8')

        def cli(*args):
            return subprocess.run([sys.executable, str(ROOT / 'scripts/cnf-agent.py'), '--db', str(self.path),
                                   *args], capture_output=True, text=True, encoding='utf-8',
                                  env=dict(os.environ, PYTHONIOENCODING='utf-8'), timeout=15)

        initial = cli('run', '--replay', str(replay), '--scenario', 'missing', '--message', first.message)
        self.assertEqual(initial.returncode, 0, initial.stderr)
        rows = self.memory.db.execute('SELECT id FROM sessions').fetchall()
        session_id = rows[0]['id']
        continued = cli('run', '--replay', str(replay), '--scenario', 'missing', '--session', session_id,
                        '--message', second.message)
        self.assertEqual(continued.returncode, 0, continued.stderr)
        self.assertIn('succeeded', cli('show', session_id).stdout)
        output = Path(self.temp.name) / 'cli-events.jsonl'
        self.assertEqual(cli('export', session_id, '--output', str(output)).returncode, 0)
        self.assertTrue(output.is_file())
        self.assertEqual(cli('demo', '--replay', str(replay), '--scenario', 'missing').returncode, 0)

    def test_cli_can_query_uncertain_operation_without_new_message_or_execution(self):
        replay = ROOT / 'tests/fixtures/cnf-agent-replay.json'
        recorded = frame()
        _, _, result = self.run_frame(recorded, tools=SimulatedTools('result_unknown'))
        command = [sys.executable, str(ROOT / 'scripts/cnf-agent.py'), '--db', str(self.path),
                   'run', '--replay', str(replay), '--scenario', 'result_unknown', '--session', result.id]
        run = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(run.returncode, 1, run.stderr)
        queried = self.memory.load(result.id)
        self.assertEqual(queried.phase, Phase.UNKNOWN)
        self.assertEqual(queried.usage.queries, result.usage.queries + 1)
        self.assertEqual(queried.usage.experiments, 1)
        self.assertEqual(queried.operation_id, result.operation_id)
        self.assertEqual(queried.messages, result.messages)
        invalid = subprocess.run(command[:-2], capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(invalid.returncode, 2)


if __name__ == '__main__':
    unittest.main()
