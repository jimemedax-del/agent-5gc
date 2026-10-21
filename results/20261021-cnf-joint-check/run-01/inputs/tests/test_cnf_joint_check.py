"""Joint-check acceptance and mutation boundaries; no cluster access."""
import copy
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


j = load('joint_tests', ROOT / 'scripts/cnf-joint-check.py')
a = load('joint_audit_tests', ROOT / 'scripts/audit-cnf-joint-check.py')


def rows(phase, core=4.82, edge=14.37):
    return [{'outcome': 'passed', 'businessClass': name, 'receiverMbps': value}
            for _ in range(phase['roundsPerClass']) for name, value in [('core', core), ('edge', edge)]]


class JointCheckTests(unittest.TestCase):
    def test_overlay_changes_only_two_backend_templates(self):
        original = j.base.render('protected')
        actual = j.render()
        self.assertEqual(len(original), len(actual))
        for before, after in zip(original, actual):
            if before['kind'] != 'Deployment' or before['metadata']['name'] not in j.BACKENDS:
                self.assertEqual(before, after)
            else:
                reverted = copy.deepcopy(after)
                pod = reverted['spec']['template']
                helper = pod['spec']['containers'].pop()
                del pod['metadata']['annotations']['cnf.lab/joint-version']
                if not pod['metadata']['annotations']:
                    del pod['metadata']['annotations']
                self.assertEqual(before, reverted)
                self.assertEqual(helper['command'], ['sleep', 'infinity'])
                self.assertEqual(helper['securityContext']['capabilities'], {'drop': ['ALL'], 'add': ['NET_ADMIN']})
                self.assertNotIn('tbf', ' '.join(helper['command']))

    def test_frozen_load_cannot_be_silently_changed(self):
        spec = j.config()
        spec['offeredMbps'] = 100
        with patch.object(j.json, 'loads', return_value=spec):
            with self.assertRaisesRegex(RuntimeError, 'workload'):
                j.config()

    def test_shaped_acceptance(self):
        spec = j.config()
        phase = spec['phases'][1]
        self.assertGreater(j.summarize(rows(phase), phase, spec)['medianRatio'], 2)

    def test_both_collapsed_rates_do_not_pass_ratio_alone(self):
        spec = j.config()
        phase = spec['phases'][1]
        with self.assertRaisesRegex(RuntimeError, 'bounds'):
            j.summarize(rows(phase, .1, .3), phase, spec)

    def test_excess_shaped_rate_is_rejected(self):
        spec = j.config()
        phase = spec['phases'][1]
        with self.assertRaisesRegex(RuntimeError, 'bounds'):
            j.summarize(rows(phase, 6, 18), phase, spec)

    def test_unshaped_insufficient_capacity_is_not_qos_success(self):
        spec = j.config()
        for phase in [spec['phases'][0], spec['phases'][2]]:
            with self.assertRaisesRegex(RuntimeError, '18 Mbps'):
                j.summarize(rows(phase, 17, 20), phase, spec)

    def test_incomplete_or_failed_batch_is_rejected(self):
        spec = j.config()
        phase = spec['phases'][1]
        sample = rows(phase)
        for invalid in [sample[:-1], [{**r, 'outcome': 'failed'} for r in sample]]:
            with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
                j.summarize(invalid, phase, spec)

    def test_tbf_wrong_rate_and_foreign_unshaped_queue_rejected(self):
        q = {'kind': 'tbf', 'root': True, 'handle': '1:',
             'options': {'rate': 625000, 'burst': '32Kb/1', 'lat': 100000}}
        self.assertEqual(a.raw_queue([q], True, 5), q)
        for value, shaped, rate in [([q], True, 15), ([q], False, 5),
                                   ([{**q, 'options': {**q['options'], 'lat': 50000}}], True, 5)]:
            with self.assertRaises(ValueError):
                a.raw_queue(value, shaped, rate)

    def test_restore_refuses_foreign_backend_before_apply(self):
        original = [d for d in j.base.render('protected') if d['kind'] == 'Deployment' and d['metadata']['name'] in j.BACKENDS]
        joint = [d for d in j.render() if d['kind'] == 'Deployment' and d['metadata']['name'] in j.BACKENDS]
        objects = {(d['kind'], d['metadata']['name']): copy.deepcopy(d) for d in joint}
        objects['Deployment', j.BACKENDS[0]]['spec']['template']['spec']['containers'][0]['image'] = 'foreign:latest'
        with TemporaryDirectory() as directory, patch.object(j.base, 'inventory', return_value=objects), patch.object(j.m1, 'kube') as kube:
            with self.assertRaisesRegex(RuntimeError, 'Foreign backend'):
                j.restore(Path(directory) / 'restore', original, joint, {})
            kube.assert_not_called()

    def test_formal_failure_still_restores_and_keeps_failed_result(self):
        from contextlib import ExitStack
        observation = {'pods': {}, 'queueMode': 'none', 'resources': [], 'bindings': {}, 'queueShape': {},
                       'uplinkClean': True, 'nativeQos': 'QoS Enable: 0', 'endpoint': {}}
        class Backend:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def full(self, folder):
                return observation
        with TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(j.live, 'Backend', Backend))
            for obj, name in [(j.base, 'verify'), (j, 'wait_rollouts'),
                              (j.m1, 'assert_steady'), (j, 'verify_joint')]:
                stack.enter_context(patch.object(obj, name))
            stack.enter_context(patch.object(j, 'unaffected', side_effect=lambda value: value))
            stack.enter_context(patch.object(j.m1, 'kube', return_value=type('Result', (), {'stdout': ''})()))
            stack.enter_context(patch.object(j.c2, 'snapshot', return_value={}))
            stack.enter_context(patch.object(j.np, 'runtime', return_value={}))
            stack.enter_context(patch.object(j, 'measure_phase', side_effect=RuntimeError('actual failure')))
            restored = stack.enter_context(patch.object(j, 'restore'))
            folder = Path(directory)
            with self.assertRaisesRegex(RuntimeError, 'actual failure'):
                j.run(folder)
            restored.assert_called_once()
            summary = a.read(folder / 'summary.json')
            self.assertEqual(summary['outcome'], 'failed')
            self.assertEqual(summary['restoration'], 'passed')
            self.assertEqual(summary['strictFinal'], 'passed')


if __name__ == '__main__':
    unittest.main()
