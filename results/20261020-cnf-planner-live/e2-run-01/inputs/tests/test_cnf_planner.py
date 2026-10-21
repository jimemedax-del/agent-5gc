"""E1 offline contract and adversarial planning checks; no cluster operations."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('cnf_planner', ROOT / 'scripts/cnf-planner.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
AT = '2026-10-20T12:00:00+08:00'


def source(direction='downlink'):
    return {'sourceVersion': 'cnf-planner-state-input-v1', 'mode': 'declared_fixture', 'direction': direction,
            'declaredAt': AT, 'ttlSeconds': 180, 'capacityMbps': 10, 'unmanagedReservedMbps': 0,
            'unmanagedTrafficDetected': False, 'nativeQosEnabled': False, 'activeProfile': 'none',
            'bindings': [{'slot': slot, 'user_id': slot, 'session_id': 'declared-session-' + slot} for slot in tool.SLOTS]}


def batch(direction='downlink'):
    return {'requestVersion': 'cnf-planner-requests-v1', 'batch_id': 'unit-batch',
            'assessmentScope': 'controlled_receiver_steady_mean', 'protocol': 'UDP',
            'requests': [{'request_id': 'request-' + slot, 'user_id': slot, 'session_id': 'declared-session-' + slot,
                          'requirements': {'direction': direction, 'min_bandwidth_mbps': 4, 'max_bandwidth_mbps': 8,
                                           'priority': 'normal', 'valid_for_seconds': 30, 'measurement_window_seconds': 20}}
                         for slot in tool.SLOTS]}


def reseal(value, kind):
    return tool.seal({k: v for k, v in value.items() if k not in [kind + 'Id', kind + 'Hash']}, kind)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.state = tool.inspect_state(source(), AT)['state']
        self.requests = batch()

    def plan(self):
        return tool.plan_policy(self.requests, self.state, AT)

    def validation(self, plan, at=AT, state=None):
        return tool.validate_plan(plan, self.requests, state or self.state, at)

    def test_equal_requests_choose_equal_and_validate_offline(self):
        result = self.plan()
        self.assertEqual(result['status'], 'planned')
        self.assertEqual(result['plan']['mode'], 'equal')
        self.assertFalse(result['executionAuthorized'])
        self.assertFalse(self.state['liveBindingsVerified'])
        check = self.validation(result['plan'])
        self.assertEqual(check['status'], 'valid_offline')
        self.assertFalse(check['executionAuthorized'])

    def test_both_directions_have_separate_state_and_endpoint(self):
        up_state = tool.inspect_state(source('uplink'), AT)['state']
        result = tool.plan_policy(batch('uplink'), up_state, AT)
        self.assertEqual(result['plan']['action']['namespace'], 'cnf-contention-uplink')
        self.assertEqual(result['plan']['action']['queueDevice'], 'cnfc2ifb0')
        self.assertEqual(tool.validate_plan(result['plan'], batch('uplink'), up_state, AT)['status'], 'valid_offline')

    def test_guarantee_role_and_priority_exchange_follow_user(self):
        for high in ['ue1', 'ue2']:
            for r in self.requests['requests']:
                is_high = r['user_id'] == high
                r['requirements'].update(min_bandwidth_mbps=5 if is_high else 1.5,
                                         max_bandwidth_mbps=8 if is_high else 4,
                                         priority='high' if is_high else 'low')
            result = self.plan()
            self.assertEqual(result['plan']['mode'], 'guarantee-' + high)
            self.assertEqual(self.validation(result['plan'])['status'], 'valid_offline')

    def test_priority_only_does_not_claim_borrowed_throughput_as_guarantee(self):
        for i, r in enumerate(self.requests['requests']):
            r['requirements'].update(min_bandwidth_mbps=1, max_bandwidth_mbps=10,
                                     priority='high' if i == 0 else 'low')
        result = self.plan()
        self.assertEqual(result['plan']['mode'], 'priority-ue1')
        self.assertEqual([a['referenceMinMbps'] for a in result['plan']['allocations']], [1.7, 1.7])

    def test_joint_six_plus_six_rejected_without_reducing_demand(self):
        for r in self.requests['requests']:
            r['requirements']['min_bandwidth_mbps'] = 6
        before = copy.deepcopy(self.requests)
        result = self.plan()
        self.assertEqual(result['status'], 'rejected')
        self.assertIsNone(result['plan'])
        self.assertTrue(all('requested_minima_exceed_capacity_with_margin' in v for v in result['candidateEvaluations'].values()))
        self.assertEqual(self.requests, before)

    def test_capacity_margin_boundary_is_strict(self):
        for minimum, exceeded in [(4.75, False), (4.75001, True)]:
            for r in self.requests['requests']:
                r['requirements']['min_bandwidth_mbps'] = minimum
            reasons = self.plan()['candidateEvaluations']['equal']
            self.assertEqual('requested_minima_exceed_capacity_with_margin' in reasons, exceeded)

    def test_mathematical_capacity_does_not_imply_available_fixed_profile(self):
        for r in self.requests['requests']:
            r['requirements']['min_bandwidth_mbps'] = 4.5
        result = self.plan()
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(result['issues'][0]['code'], 'no_compatible_validated_profile')

    def test_maximum_cap_blocks_profiles_with_higher_ceiling(self):
        for r in self.requests['requests']:
            r['requirements'].update(min_bandwidth_mbps=1, max_bandwidth_mbps=2)
        self.assertEqual(self.plan()['status'], 'rejected')

    def test_missing_priority_needs_clarification_instead_of_default(self):
        self.requests['requests'][0]['requirements'].pop('priority')
        result = self.plan()
        self.assertEqual(result['status'], 'needs_clarification')
        self.assertIsNone(result['plan'])

    def test_missing_user_or_session_is_not_invented_from_fixture_slot(self):
        for field in ['user_id', 'session_id']:
            self.requests = batch()
            self.requests['requests'][0][field] = None
            result = self.plan()
            self.assertEqual(result['status'], 'needs_clarification')
            self.assertEqual(result['issues'][0]['code'], 'missing_subject')

    def test_unknown_user_stale_session_and_duplicate_users_rejected(self):
        for field, value in [('user_id', 'unknown'), ('session_id', 'old-session')]:
            self.requests = batch()
            self.requests['requests'][0][field] = value
            self.assertEqual(self.plan()['status'], 'rejected')
        self.requests = batch()
        self.requests['requests'][1].update(user_id='ue1', session_id='declared-session-ue1')
        self.assertEqual(self.plan()['issues'][0]['code'], 'duplicate_user')

    def test_unsupported_qos_deadline_and_bidir_targets_rejected(self):
        for field, value in [('direction', 'bidirectional'), ('rtt_p95_max_ms', 20), ('one_way_p95_max_ms', 10),
                             ('loss_max_percent', 0), ('deadline_seconds', 20)]:
            self.requests = batch()
            self.requests['requests'][0]['requirements'][field] = value
            self.assertEqual(self.plan()['status'], 'rejected', field)

    def test_unsupported_duration_window_transport_and_metric_rejected(self):
        for name, value in [('valid_for_seconds', 1800), ('measurement_window_seconds', 10)]:
            self.requests = batch()
            self.requests['requests'][0]['requirements'][name] = value
            self.assertEqual(self.plan()['issues'][0]['code'], 'unsupported_time_scope')
        self.requests = batch()
        self.requests['protocol'] = 'TCP'
        self.assertEqual(self.plan()['issues'][0]['code'], 'unsupported_transport')
        self.requests = batch()
        self.requests['assessmentScope'] = 'end_to_end_sla'
        self.assertEqual(self.plan()['issues'][0]['code'], 'unsupported_assessment_scope')

    def test_boolean_nonfinite_negative_and_conflicting_targets_rejected(self):
        for value in [True, float('nan'), float('inf'), 0, -1, '4', None]:
            self.requests = batch()
            self.requests['requests'][0]['requirements']['min_bandwidth_mbps'] = value
            self.assertNotEqual(self.plan()['status'], 'planned', repr(value))
        self.requests = batch()
        self.requests['requests'][0]['requirements']['min_bandwidth_mbps'] = 9
        self.assertEqual(self.plan()['issues'][0]['code'], 'conflicting_bounds')

    def test_unknown_fields_and_injected_commands_rejected(self):
        self.requests['requests'][0]['requirements']['command'] = 'arbitrary action'
        self.assertEqual(self.plan()['issues'][0]['code'], 'unknown_target')

    def test_state_expiration_and_insufficient_remaining_horizon(self):
        self.assertEqual(tool.plan_policy(self.requests, self.state, '2026-10-20T12:03:01+08:00')['issues'][0]['code'], 'stale_state')
        self.assertEqual(tool.plan_policy(self.requests, self.state, '2026-10-20T12:02:40+08:00')['issues'][0]['code'], 'insufficient_state_horizon')

    def test_native_qos_unmanaged_traffic_and_unknown_existing_policy_rejected(self):
        for field, value in [('nativeQosEnabled', True), ('unmanagedTrafficDetected', True),
                             ('unmanagedReservedMbps', .1), ('activeProfile', 'unmanaged')]:
            value_source = source()
            value_source[field] = value
            result = tool.inspect_state(value_source, AT)
            self.assertEqual(result['status'], 'rejected')

    def test_live_source_claim_is_rejected(self):
        value = source()
        value['mode'] = 'live'
        self.assertEqual(tool.inspect_state(value, AT)['issues'][0]['code'], 'unsupported_state_source')

    def test_snapshot_and_plan_hash_tampering_rejected(self):
        plan = self.plan()['plan']
        plan['allocations'][0]['queue']['rate'] = 10
        self.assertEqual(self.validation(plan)['issues'][0]['code'], 'content_hash_mismatch')
        self.state['source']['activeProfile'] = 'equal'
        self.assertEqual(self.plan()['issues'][0]['code'], 'content_hash_mismatch')

    def test_rehashed_bad_allocations_actions_targets_and_identity_still_rejected(self):
        original = self.plan()['plan']
        variants = []
        value = copy.deepcopy(original)
        value['allocations'][0]['queue']['rate'] = 9
        variants.append(value)
        value = copy.deepcopy(original)
        value['action']['namespace'] = 'free5gc'
        variants.append(value)
        value = copy.deepcopy(original)
        value['allocations'][0]['user_id'] = 'ue2'
        variants.append(value)
        value = copy.deepcopy(original)
        value['verificationTarget']['steadyEndSeconds'] = 24
        variants.append(value)
        for value in variants:
            self.assertEqual(self.validation(reseal(value, 'plan'))['issues'][0]['code'], 'plan_semantics_mismatch')

    def test_rehashed_unknown_mode_and_missing_fields_rejected(self):
        value = self.plan()['plan']
        value['mode'] = 'arbitrary'
        self.assertEqual(self.validation(reseal(value, 'plan'))['issues'][0]['code'], 'unknown_policy_mode')
        value = self.plan()['plan']
        value.pop('validUntil')
        self.assertEqual(self.validation(reseal(value, 'plan'))['issues'][0]['code'], 'invalid_fields')

    def test_changed_state_or_request_invalidates_old_plan(self):
        plan = self.plan()['plan']
        value = source()
        value['activeProfile'] = 'equal'
        state = tool.inspect_state(value, AT)['state']
        self.assertEqual(self.validation(plan, state=state)['issues'][0]['code'], 'plan_binding_mismatch')
        self.requests['requests'][0]['requirements']['min_bandwidth_mbps'] = 3
        self.assertEqual(self.validation(plan)['issues'][0]['code'], 'plan_binding_mismatch')

    def test_expired_plan_is_rejected_even_with_fresh_state(self):
        plan = self.plan()['plan']
        self.assertEqual(self.validation(plan, at='2026-10-20T12:00:31+08:00')['issues'][0]['code'], 'expired_plan')

    def test_request_order_does_not_change_plan(self):
        first = self.plan()['plan']
        self.requests['requests'].reverse()
        self.assertEqual(first, self.plan()['plan'])

    def test_unsupported_execution_tools_never_report_success(self):
        for result in [tool.apply_plan({}, {}, {}), tool.verify_plan({}, {}), tool.restore({}, {})]:
            self.assertEqual(result['status'], 'unsupported')
            self.assertFalse(result['executionAuthorized'])
            self.assertFalse(result['networkChanged'])

    def test_import_bridge_does_not_trust_classifier_candidate_flag(self):
        loader = importlib.util.spec_from_file_location('requirements_for_planner', ROOT / 'scripts/cnf-requirements.py')
        normalizer = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(normalizer)
        entries = []
        for r in self.requests['requests']:
            raw = {'request_id': r['request_id'], 'description': '视频播放',
                   'subject': {'user_id': r['user_id'], 'session_id': r['session_id']}, 'requested': r['requirements']}
            entries.append(normalizer.normalize(raw))
        imported = tool.import_requirements(entries, 'imported', 'UDP', 'controlled_receiver_steady_mean')
        self.assertEqual(tool.plan_policy(imported, self.state, AT)['status'], 'planned')
        entries[0]['requirements']['priority'].update(value=None, resolution='unknown')
        entries[0]['capabilityReview']['candidateForEPlanning'] = True
        imported = tool.import_requirements(entries, 'imported', 'UDP', 'controlled_receiver_steady_mean')
        self.assertEqual(tool.plan_policy(imported, self.state, AT)['status'], 'needs_clarification')

    def test_import_bridge_refuses_conflicts_or_implicit_transport_scope(self):
        with self.assertRaises(tool.Rejection):
            tool.import_requirements([], 'x', 'TCP', 'general_sla')

    def test_strict_json_duplicate_and_nonfinite_values(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'input.json'
            for raw in ['{"x":1,"x":2}', '{"x":NaN}']:
                path.write_text(raw, encoding='utf-8')
                with self.assertRaises(tool.Rejection):
                    tool.read_json(path)

    def test_cli_inspect_plan_validate_and_unsupported_tools(self):
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            for name, value in [('source.json', source()), ('requests.json', batch())]:
                (folder / name).write_text(json.dumps(value), encoding='utf-8')
            entry = str(ROOT / 'scripts/cnf-planner.py')
            calls = [('inspect', ['--source', str(folder / 'source.json')], 'state.json', 'ok'),
                     ('plan', ['--requests', str(folder / 'requests.json'), '--state', str(folder / 'state.json')], 'plan.json', 'planned'),
                     ('validate', ['--requests', str(folder / 'requests.json'), '--state', str(folder / 'state.json'),
                                   '--plan', str(folder / 'plan.json')], 'validation.json', 'valid_offline'),
                     ('apply', [], 'execution.json', 'unsupported')]
            for command, args, name, expected in calls:
                subprocess.run([sys.executable, entry, command, *args, '--at', AT, '--out', str(folder / name)],
                               capture_output=True, text=True, check=True)
                self.assertEqual(tool.read_json(folder / name)['status'], expected)

    def test_catalogue_queue_policies_match_the_accepted_c1_implementation(self):
        loader = importlib.util.spec_from_file_location('reference_c1_for_planner', ROOT / 'scripts/cnf-contention.py')
        reference = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(reference)
        for fixture in tool.catalogue()['directions'].values():
            for mode, profile in fixture['profiles'].items():
                self.assertEqual(profile['queuePolicy'], reference.policy(mode))

    def test_malformed_shapes_and_rehashed_bad_mode_return_structured_rejection(self):
        for value in [None, [], 'state', {'sourceVersion': 'partial'}]:
            self.assertEqual(tool.inspect_state(value, AT)['status'], 'rejected')
        raw = source()
        raw['direction'] = []
        self.assertEqual(tool.inspect_state(raw, AT)['status'], 'rejected')
        value = self.plan()['plan']
        value['mode'] = []
        self.assertEqual(self.validation(reseal(value, 'plan'))['status'], 'rejected')
        broken = copy.deepcopy(self.state)
        broken['source']['capacityMbps'] = float('nan')
        self.assertEqual(tool.plan_policy(self.requests, broken, AT)['status'], 'rejected')

    def test_import_bridge_reports_malformed_output_as_contract_error(self):
        with self.assertRaises(tool.Rejection):
            tool.import_requirements([{'contractVersion': 'cnf-requirements-v1'}], 'batch', 'UDP', 'controlled_receiver_steady_mean')

    def test_cli_accepts_import_tool_envelope_without_manual_json_rewriting(self):
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            values = {'state.json': self.state, 'imported.json': tool.envelope('import_requirements', 'ok', requests=batch())}
            for name, value in values.items():
                (folder / name).write_text(json.dumps(value), encoding='utf-8')
            subprocess.run([sys.executable, str(ROOT / 'scripts/cnf-planner.py'), 'plan',
                            '--requests', str(folder / 'imported.json'), '--state', str(folder / 'state.json'),
                            '--at', AT, '--out', str(folder / 'plan.json')], check=True, capture_output=True, text=True)
            self.assertEqual(tool.read_json(folder / 'plan.json')['status'], 'planned')


if __name__ == '__main__':
    unittest.main()
