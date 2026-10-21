"""E2 negative boundaries and restoration; never access a cluster."""
import copy
from datetime import timedelta
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('live_under_test', ROOT / 'scripts/cnf-planner-live.py')
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)
AT = live.p.instant('2026-10-20T13:30:00Z')
EQUAL = ROOT / 'results/20261020-cnf-contention/measure-01/01-equal/tc-after.json'
NONE = {'qdisc': [{'kind': 'noqueue', 'handle': '0:', 'root': True}], 'class': [], 'filter': []}


def reseal(value, kind):
    return live.p.seal({k: v for k, v in value.items() if k not in [kind + 'Id', kind + 'Hash']}, kind)


def observation():
    return {'queueMode': 'none', 'bindings': {slot: {'supi': 'imsi-20893000000000' + str(i),
            'session_id': 'pdu-session-' + slot} for i, slot in enumerate(['ue1', 'ue2'], 1)}, 'endpoint': {'uid': 'endpoint-uid'}}


class FakeBackend:
    def __init__(self, observed, tc=None):
        self.locked, self.mutation_attempted, self.apply_completed = True, False, False
        self.observed, self.current = observed, copy.deepcopy(tc or NONE)
        self.writes, self.fail_write, self.fail_pod = [], False, False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.locked = False

    def full(self, folder):
        folder.mkdir()
        return self.observed

    def check_pod(self):
        if self.fail_pod:
            raise ValueError('endpoint_replaced')

    def observe(self, folder):
        folder.mkdir()
        return self.observed

    def tc(self):
        return self.current

    def write_batch(self, lines, folder):
        self.writes.append(lines)
        self.current = copy.deepcopy(NONE if lines[0].startswith('qdisc del') else live.read(EQUAL))
        if self.fail_write:
            self.fail_write = False
            raise ValueError('ordinary TC failure')


class LivePlannerTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch.object(live, 'now', return_value=AT)
        self.pclock = patch.object(live.p, 'clock', return_value=AT)
        self.clock.start()
        self.pclock.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(self.pclock.stop)
        self.observed = observation()
        self.state = live.live_state(self.observed)
        self.batch = live.requests_for(self.state)
        self.plan = live.plan_policy(self.state, self.batch)['plan']

    def test_observed_bindings_make_equal_plan_and_live_revalidation(self):
        self.assertEqual(self.plan['innerPlan']['mode'], 'equal')
        self.assertFalse(self.plan['computationState']['liveStateObserved'])
        self.assertEqual(live.validate_plan(self.plan, self.batch, self.state, self.observed)['status'], 'valid_live_preconditions')

    def test_pod_session_resource_and_queue_drift_refused(self):
        for key in ['bindings', 'pods', 'resources', 'queueMode', 'endpoint']:
            drift = copy.deepcopy(self.observed)
            drift[key] = 'changed'
            with self.subTest(key=key), self.assertRaises(live.p.Rejection):
                live.validate_plan(self.plan, self.batch, self.state, drift)

    def test_state_timestamp_extension_or_false_live_claim_refused(self):
        for field, value in [('liveStateObserved', False), ('bindingScope', 'global_5g_uuid'),
                             ('validUntil', (AT + timedelta(seconds=300)).isoformat()), ('command', 'anything')]:
            state = copy.deepcopy(self.state)
            state[field] = value
            with self.subTest(field=field), self.assertRaises(live.p.Rejection):
                live.check_state(reseal(state, 'liveState'))

    def test_stale_state_and_expired_plan_refused(self):
        for seconds in [31, 181]:
            with patch.object(live, 'now', return_value=AT + timedelta(seconds=seconds)), \
                 patch.object(live.p, 'clock', return_value=AT + timedelta(seconds=seconds)), self.assertRaises(live.p.Rejection):
                live.validate_plan(self.plan, self.batch, self.state, self.observed)

    def test_modified_targets_and_tampered_or_resealed_inner_action_refused(self):
        changed = copy.deepcopy(self.batch)
        changed['requests'][0]['requirements']['priority'] = 'high'
        with self.assertRaises(live.p.Rejection):
            live.plan_policy(self.state, changed)
        for rehash in [False, True]:
            plan = copy.deepcopy(self.plan)
            plan['innerPlan']['action']['namespace'] = 'free5gc'
            if rehash:
                plan['innerPlan'] = reseal(plan['innerPlan'], 'plan')
                plan = reseal(plan, 'livePlan')
            with self.assertRaises(live.p.Rejection):
                live.validate_plan(plan, self.batch, self.state, self.observed)

    def test_adapter_change_refused(self):
        with patch.object(live, 'adapter_hash', return_value='changed'), self.assertRaises(live.p.Rejection):
            live.validate_plan(self.plan, self.batch, self.state, self.observed)

    def test_session_digest_ignores_only_pending_flag(self):
        user = {'pod': 'ue', 'podUID': 'uid', 'ip': '10.60.0.3'}
        listing = 'PDU Session1\n state: PS-ACTIVE\n address: 10.60.0.3\n ambr: up[1000Mb/s] down[1000Mb/s]\n data-pending: false\n'
        first = live.session_record('supi', user, listing)
        self.assertEqual(first, live.session_record('supi', user, listing.replace('false', 'true')))
        for changed in [listing.replace('PS-ACTIVE', 'PS-INACTIVE'), listing.replace('.3', '.4'), listing + 'PDU Session2\n']:
            with self.assertRaises(live.p.Rejection):
                live.session_record('supi', user, changed)

    def test_apply_requires_lock_and_fresh_fingerprint(self):
        for locked, observed in [(False, self.observed), (True, {'changed': True})]:
            backend = FakeBackend(observed)
            backend.locked = locked
            with TemporaryDirectory() as tmp, self.assertRaises(live.p.Rejection):
                live.apply_plan(self.plan, self.batch, self.state, backend, Path(tmp) / 'apply')
            self.assertFalse(backend.mutation_attempted)
            self.assertFalse(backend.writes)

    def test_apply_equal_and_restore_original_noqueue(self):
        backend = FakeBackend(self.observed)
        with TemporaryDirectory() as tmp:
            self.assertEqual(live.apply_plan(self.plan, self.batch, self.state, backend, Path(tmp) / 'apply')['status'], 'applied')
            self.assertEqual(live.restore(self.state, backend, Path(tmp) / 'restore')['status'], 'restored')
        self.assertEqual(backend.writes, [live.c.tc_commands('equal'), ['qdisc del dev eth0 root handle 1:']])

    def test_apply_error_preserves_intent_and_allows_owned_cleanup(self):
        backend = FakeBackend(self.observed)
        backend.fail_write = True
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'ordinary TC failure'):
                live.apply_plan(self.plan, self.batch, self.state, backend, Path(tmp) / 'apply')
            self.assertTrue(backend.mutation_attempted)
            self.assertFalse(backend.apply_completed)
            self.assertEqual(live.restore(self.state, backend, Path(tmp) / 'restore')['status'], 'restored')

    def test_restore_refuses_foreign_queue_missing_ownership_and_replaced_pod(self):
        for variant in ['foreign', 'unowned', 'replaced', 'unlocked']:
            backend = FakeBackend(self.observed, live.read(EQUAL))
            backend.mutation_attempted = variant != 'unowned'
            backend.fail_pod = variant == 'replaced'
            backend.locked = variant != 'unlocked'
            if variant == 'foreign':
                backend.current['qdisc'][0]['kind'] = 'foreign'
            with TemporaryDirectory() as tmp, self.subTest(variant=variant), self.assertRaises((ValueError, live.p.Rejection)):
                live.restore(self.state, backend, Path(tmp) / 'restore')
            self.assertFalse(backend.writes)

    def test_restore_noqueue_is_noop_and_expiry_does_not_block_owned_cleanup(self):
        backend = FakeBackend(self.observed)
        with TemporaryDirectory() as tmp:
            live.restore(self.state, backend, Path(tmp) / 'none')
            self.assertFalse(backend.writes)
            backend.current = live.read(EQUAL)
            backend.mutation_attempted, backend.apply_completed = True, True
            with patch.object(live, 'now', return_value=AT + timedelta(hours=1)):
                live.restore(self.state, backend, Path(tmp) / 'expired')

    def test_restore_drift_is_failure_even_when_queue_cleared(self):
        backend = FakeBackend({'changed': True}, live.read(EQUAL))
        backend.mutation_attempted, backend.apply_completed = True, True
        with TemporaryDirectory() as tmp, self.assertRaises(live.p.Rejection):
            live.restore(self.state, backend, Path(tmp) / 'restore')
        self.assertEqual(backend.current, NONE)

    def test_known_partial_root_and_classes_accepted_but_foreign_parts_refused(self):
        equal = live.read(EQUAL)
        self.assertTrue(live.owned_partial(equal))
        for count in range(1, 5):
            partial = {**copy.deepcopy(equal), 'qdisc': equal['qdisc'][:1], 'class': equal['class'][:count], 'filter': []}
            partial.pop('raw')
            self.assertTrue(live.owned_partial(partial))
        for field in ['class', 'filter', 'qdisc']:
            changed = copy.deepcopy(equal)
            changed.pop('raw')
            changed[field][0]['kind'] = 'foreign'
            with self.assertRaises((ValueError, live.p.Rejection)):
                live.owned_partial(changed)

    def test_verify_reuses_raw_receiver_evidence_and_applies_actual_targets(self):
        base = ROOT / 'results/20261020-cnf-contention/measure-01'
        topology = live.read(base / 'initial/topology.json')
        batch = copy.deepcopy(self.batch)
        for slot, r in zip(['ue1', 'ue2'], batch['requests']):
            r['user_id'] = topology['users'][slot]['supi']
        value = live.verify_plan(self.plan, batch, topology, base / '01-equal')
        self.assertEqual(value['status'], 'passed_controlled_targets')
        batch['requests'][0]['requirements']['min_bandwidth_mbps'] = 6
        self.assertEqual(live.verify_plan(self.plan, batch, topology, base / '01-equal')['status'], 'failed_targets')

    def test_whole_run_capture_failure_always_restores_and_preserves_error(self):
        backend = FakeBackend(self.observed)
        with TemporaryDirectory() as tmp, patch.object(live.sys, 'platform', 'linux'), \
             patch.object(live, 'Backend', return_value=backend), patch.object(live, 'archive_inputs'), \
             patch.object(live, 'capture', side_effect=ValueError('capture failed')):
            folder = Path(tmp) / 'run'
            with self.assertRaisesRegex(ValueError, 'capture failed'):
                live.run(folder)
            self.assertEqual(live.read(folder / 'summary.json')['restoration'], 'restored')
            self.assertEqual(live.read(folder / 'execution-error.json')['error'], 'capture failed')
            self.assertEqual(backend.current, NONE)

    def test_whole_run_failed_restore_never_reports_success(self):
        backend = FakeBackend(self.observed)
        with TemporaryDirectory() as tmp, patch.object(live.sys, 'platform', 'linux'), \
             patch.object(live, 'Backend', return_value=backend), patch.object(live, 'archive_inputs'), \
             patch.object(live, 'capture', side_effect=ValueError('capture failed')), \
             patch.object(live, 'restore', side_effect=ValueError('foreign queue')):
            folder = Path(tmp) / 'run'
            with self.assertRaisesRegex(ValueError, 'foreign queue'):
                live.run(folder)
            self.assertEqual(live.read(folder / 'summary.json')['outcome'], 'failed')
            self.assertEqual(live.read(folder / 'execution-error.json')['error'], 'capture failed')
            self.assertEqual(live.read(folder / 'restoration.json')['status'], 'failed')

    def test_extra_initial_qdisc_refused(self):
        tc = copy.deepcopy(NONE)
        tc['qdisc'].append({'kind': 'ingress', 'handle': 'ffff:'})
        with self.assertRaises(live.p.Rejection):
            live.mode_of(tc)


if __name__ == '__main__':
    unittest.main()
