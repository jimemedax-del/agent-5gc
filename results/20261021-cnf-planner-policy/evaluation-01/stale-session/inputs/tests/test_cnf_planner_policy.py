"""E3 caller-supplied requests, whitelist selection, ownership and raw targets."""
import copy
from datetime import timedelta
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('policy_tests', ROOT / 'scripts/cnf-planner-policy.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
live = tool.live
AT = live.p.instant('2026-10-21T03:00:00Z')
BASE = ROOT / 'results/20261020-cnf-contention/measure-01'
NONE = {'qdisc': [{'kind': 'noqueue', 'root': True, 'handle': '0:'}], 'class': [], 'filter': []}


def observed():
    return {'queueMode': 'none', 'endpoint': {'uid': 'endpoint'}, 'bindings': {
        slot: {'supi': 'imsi-20893000000000' + str(i), 'session_id': 'pdu-' + slot}
        for i, slot in enumerate(['ue1', 'ue2'], 1)}}


def requests(state, high=None):
    return {'requestVersion': 'cnf-planner-requests-v1', 'batch_id': 'caller-batch',
            'assessmentScope': 'controlled_receiver_steady_mean', 'protocol': 'UDP', 'requests': [
                {'request_id': 'call-' + slot, 'user_id': b['supi'], 'session_id': b['session_id'],
                 'requirements': {'direction': 'downlink', 'min_bandwidth_mbps': (5 if slot == high else 1.5) if high else 4,
                                  'max_bandwidth_mbps': (8 if slot == high else 4) if high else 8,
                                  'priority': ('high' if slot == high else 'low') if high else 'normal',
                                  'valid_for_seconds': 30, 'measurement_window_seconds': 20}}
                for slot, b in sorted(state['observation']['bindings'].items())]}


def reseal(value, kind):
    return live.p.seal({k: v for k, v in value.items() if k not in [kind + 'Id', kind + 'Hash']}, kind)


class FakeBackend:
    def __init__(self, observation):
        self.observation = observation
        self.locked, self.mutation_attempted, self.apply_completed = True, False, False
        self.applied_mode, self.current, self.writes = 'equal', copy.deepcopy(NONE), []
        self.fail_write = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.locked = False

    def full(self, folder):
        folder.mkdir()
        return self.observation

    def observe(self, folder):
        folder.mkdir()
        return self.observation

    def check_pod(self):
        pass

    def tc(self):
        return self.current

    def write_batch(self, lines, folder):
        self.writes.append(lines)
        self.current = copy.deepcopy(NONE) if lines[0].startswith('qdisc del') else live.read(BASE / ('01-' + self.applied_mode) / 'tc-after.json')
        if self.fail_write:
            self.fail_write = False
            raise ValueError('apply failed')


class PolicyTests(unittest.TestCase):
    def setUp(self):
        a, b = patch.object(live, 'now', return_value=AT), patch.object(live.p, 'clock', return_value=AT)
        a.start(); b.start()
        self.addCleanup(a.stop); self.addCleanup(b.stop)
        self.observation = observed()
        self.state = live.live_state(self.observation)

    def plan(self, batch):
        return tool.plan_policy(self.state, batch)['plan']

    def test_equal_and_both_guarantee_roles_select_and_validate(self):
        for high, mode in [(None, 'equal'), ('ue1', 'guarantee-ue1'), ('ue2', 'guarantee-ue2')]:
            batch = requests(self.state, high)
            plan = self.plan(batch)
            self.assertEqual(plan['innerPlan']['mode'], mode)
            self.assertEqual(tool.validate_plan(plan, batch, self.state, self.observation)['status'], 'valid_live_preconditions')

    def test_different_explicit_targets_retain_values_and_change_hash(self):
        batch = requests(self.state)
        original = self.plan(batch)
        batch['requests'][0]['requirements']['min_bandwidth_mbps'] = 3
        changed = self.plan(batch)
        self.assertEqual(changed['innerPlan']['mode'], 'equal')
        self.assertNotEqual(changed['batchHash'], original['batchHash'])
        self.assertEqual(changed['innerPlan']['allocations'][0]['requestedMinMbps'], 3)

    def test_input_order_is_canonical(self):
        batch = requests(self.state)
        first = self.plan(batch)
        batch['requests'].reverse()
        self.assertEqual(first, self.plan(batch))

    def test_whitelist_selection_does_not_execute_unopened_priority_mode(self):
        batch = requests(self.state, 'ue1')
        for r in batch['requests']:
            r['requirements'].update(min_bandwidth_mbps=1, max_bandwidth_mbps=10)
        self.assertEqual(live.p.plan_policy(batch, live.computation_state(self.state))['plan']['mode'], 'priority-ue1')
        self.assertEqual(self.plan(batch)['innerPlan']['mode'], 'guarantee-ue1')

    def test_missing_targets_and_identity_are_not_filled(self):
        for field in ['priority', 'min_bandwidth_mbps', 'max_bandwidth_mbps']:
            batch = requests(self.state)
            batch['requests'][0]['requirements'].pop(field)
            self.assertEqual(tool.plan_policy(self.state, batch)['status'], 'needs_clarification')
        batch = requests(self.state)
        batch['requests'][0]['session_id'] = None
        self.assertEqual(tool.plan_policy(self.state, batch)['status'], 'needs_clarification')

    def test_stale_or_unknown_identity_is_not_rebound(self):
        for field, value in [('session_id', 'old-session'), ('user_id', 'unknown')]:
            batch = requests(self.state)
            batch['requests'][0][field] = value
            self.assertEqual(tool.plan_policy(self.state, batch)['status'], 'rejected')

    def test_capacity_and_incompatible_caps_refused_without_lowering_demands(self):
        for minimum, maximum in [(6, 8), (1, 2)]:
            batch = requests(self.state)
            for r in batch['requests']:
                r['requirements'].update(min_bandwidth_mbps=minimum, max_bandwidth_mbps=maximum)
            original = copy.deepcopy(batch)
            result = tool.plan_policy(self.state, batch)
            self.assertEqual(result['status'], 'rejected')
            self.assertEqual(result['issues'][0]['code'], 'no_compatible_live_profile')
            self.assertEqual(batch, original)

    def test_latency_loss_direction_transport_and_injected_commands_refused(self):
        for field, value in [('rtt_p95_max_ms', 20), ('loss_max_percent', 0), ('direction', 'uplink'), ('command', 'anything')]:
            batch = requests(self.state)
            batch['requests'][0]['requirements'][field] = value
            self.assertEqual(tool.plan_policy(self.state, batch)['status'], 'rejected')
        batch = requests(self.state); batch['protocol'] = 'TCP'
        self.assertEqual(tool.plan_policy(self.state, batch)['status'], 'rejected')

    def test_modified_request_invalidates_existing_plan(self):
        batch = requests(self.state)
        plan = self.plan(batch)
        batch['requests'][0]['requirements']['min_bandwidth_mbps'] = 3
        with self.assertRaises(live.p.Rejection):
            tool.validate_plan(plan, batch, self.state, self.observation)

    def test_resealed_feasible_but_unopened_plan_is_refused(self):
        batch = requests(self.state, 'ue1')
        for r in batch['requests']:
            r['requirements'].update(min_bandwidth_mbps=1, max_bandwidth_mbps=10)
        plan = self.plan(batch)
        plan['innerPlan'] = live.p.plan_policy(batch, plan['computationState'])['plan']
        plan = reseal(plan, 'policyPlan')
        self.assertEqual(live.p.validate_plan(plan['innerPlan'], batch, plan['computationState'])['status'], 'valid_offline')
        with self.assertRaises(live.p.Rejection):
            tool.validate_plan(plan, batch, self.state, self.observation)

    def test_state_drift_and_plan_expiration_refused(self):
        batch = requests(self.state)
        plan = self.plan(batch)
        with self.assertRaises(live.p.Rejection):
            tool.validate_plan(plan, batch, self.state, {**self.observation, 'queueMode': 'equal'})
        stamp = AT + timedelta(seconds=31)
        with patch.object(live, 'now', return_value=stamp), patch.object(live.p, 'clock', return_value=stamp):
            with self.assertRaises(live.p.Rejection) as caught:
                tool.validate_plan(plan, batch, self.state, self.observation)
            self.assertEqual(caught.exception.code, 'expired_plan')

    def test_apply_and_restore_each_mode_using_actual_archived_shapes(self):
        for high in [None, 'ue1', 'ue2']:
            batch = requests(self.state, high)
            plan = self.plan(batch)
            backend = FakeBackend(self.observation)
            with TemporaryDirectory() as tmp:
                tool.apply_plan(plan, batch, self.state, backend, Path(tmp) / 'apply')
                tool.restore(self.state, backend, Path(tmp) / 'restore')
                self.assertEqual(backend.writes[0], live.c.tc_commands(plan['innerPlan']['mode']))
                self.assertEqual(backend.current, NONE)

    def test_apply_error_cleanup_is_bound_to_each_guarantee_mode(self):
        for high in ['ue1', 'ue2']:
            batch = requests(self.state, high)
            backend = FakeBackend(self.observation); backend.fail_write = True
            with TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ValueError, 'apply failed'):
                    tool.apply_plan(self.plan(batch), batch, self.state, backend, Path(tmp) / 'apply')
                tool.restore(self.state, backend, Path(tmp) / 'restore')
                self.assertEqual(backend.current, NONE)

    def test_different_guarantee_mode_is_foreign_to_restore(self):
        backend = FakeBackend(self.observation)
        backend.applied_mode = 'guarantee-ue1'
        backend.mutation_attempted, backend.apply_completed = True, True
        backend.current = live.read(BASE / '01-guarantee-ue2/tc-after.json')
        with TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            tool.restore(self.state, backend, Path(tmp) / 'restore')
        self.assertFalse(backend.writes)

    def test_same_backend_does_not_replay_after_restore(self):
        batch = requests(self.state)
        plan = self.plan(batch); backend = FakeBackend(self.observation)
        with TemporaryDirectory() as tmp:
            tool.apply_plan(plan, batch, self.state, backend, Path(tmp) / 'apply')
            tool.restore(self.state, backend, Path(tmp) / 'restore')
            with self.assertRaises(live.p.Rejection) as caught:
                tool.apply_plan(plan, batch, self.state, backend, Path(tmp) / 'replay')
            self.assertEqual(caught.exception.code, 'already_executed')

    def test_raw_receiver_targets_follow_selected_mode_and_actual_requests(self):
        topology = live.read(BASE / 'initial/topology.json')
        for high in [None, 'ue1', 'ue2']:
            batch = requests(self.state, high)
            plan = self.plan(batch)
            value = tool.verify_plan(plan, batch, topology, BASE / ('01-' + plan['innerPlan']['mode']))
            self.assertEqual(value['status'], 'passed_controlled_targets')
            self.assertEqual(value['policyPlanHash'], plan['policyPlanHash'])

    def test_rejected_request_run_is_read_only_and_checks_final_baseline(self):
        batch = requests(self.state); batch['requests'][0]['requirements'].pop('priority')
        backend = FakeBackend(self.observation)
        with TemporaryDirectory() as tmp, patch.object(tool.sys, 'platform', 'linux'), \
             patch.object(tool, 'Backend', return_value=backend), patch.object(live, 'archive_inputs'):
            value = tool.run(Path(tmp) / 'run', batch)
        self.assertEqual(value['outcome'], 'needs_clarification')
        self.assertTrue(value['finalStrictBaselinePassed'])
        self.assertFalse(backend.writes)

    def test_guarantee_measurement_failure_remains_failed_after_restore(self):
        batch = requests(self.state, 'ue2'); backend = FakeBackend(self.observation)
        with TemporaryDirectory() as tmp, patch.object(tool.sys, 'platform', 'linux'), patch.object(tool, 'Backend', return_value=backend), \
             patch.object(live, 'archive_inputs'), patch.object(live, 'capture', side_effect=ValueError('measurement failed')):
            path = Path(tmp) / 'run'
            with self.assertRaisesRegex(ValueError, 'measurement failed'):
                tool.run(path, batch)
            summary = live.read(path / 'summary.json')
            self.assertEqual(summary['outcome'], 'failed')
            self.assertEqual(summary['restoration'], 'restored')
            self.assertTrue(summary['finalStrictBaselinePassed'])
            self.assertEqual(backend.applied_mode, 'guarantee-ue2')

    def test_wrong_measurement_mode_is_not_accepted_for_another_plan(self):
        batch = requests(self.state, 'ue2'); plan = self.plan(batch)
        topology = live.read(BASE / 'initial/topology.json')
        with self.assertRaises(live.p.Rejection) as caught:
            tool.verify_plan(plan, batch, topology, BASE / '01-guarantee-ue1')
        self.assertEqual(caught.exception.code, 'measurement_mode_mismatch')

    def test_cli_plans_from_versioned_inspect_envelope_without_network_calls(self):
        state = live.live_state(self.observation, datetime.now(timezone.utc))
        batch = requests(state, 'ue1')
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            live.save(folder / 'state.json', tool.result('inspect_state', 'observed', state=state, executionAuthorized=False))
            live.save(folder / 'requests.json', batch)
            subprocess.run([sys.executable, str(ROOT / 'scripts/cnf-planner-policy.py'), 'plan', '--state', str(folder / 'state.json'),
                            '--requests', str(folder / 'requests.json'), '--out', str(folder / 'planned.json')], check=True, capture_output=True, text=True)
            value = live.read(folder / 'planned.json')
            self.assertEqual(value['status'], 'planned')
            self.assertEqual(value['plan']['innerPlan']['mode'], 'guarantee-ue1')
            self.assertFalse(value['executionAuthorized'])


if __name__ == '__main__':
    unittest.main()
