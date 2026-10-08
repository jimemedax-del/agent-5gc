import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('profile_tool', ROOT / 'scripts/free5gc-profile.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile = tool.load_profile()
        self.docs = []
        for name, entry in self.profile['workloads'].items():
            component = entry['component'].lower()
            if component == 'upf':
                image = next(i['source'] for i in self.profile['images'] if i['role'] == 'user')
            elif component == 'ue':
                image = 'local/ueransim-iperf3:free5gc-v4.0.1-20261008'
            elif component == 'gnb':
                image = 'free5gc/ueransim:v4.0.1'
            elif component == 'mongodb':
                image = 'bitnamilegacy/mongodb:7.0.9-debian-12-r4'
            else:
                image = f'free5gc/{component}:v4.2.2'
            self.docs.append({'apiVersion': 'apps/v1', 'kind': 'StatefulSet' if component == 'mongodb' else 'Deployment',
                              'metadata': {'name': name}, 'spec': {'replicas': 1, 'template': {
                                  'metadata': {}, 'spec': {'nodeSelector': copy.deepcopy(self.profile['roles'][entry['role']]),
                                  'containers': [{'name': component, 'image': image}]}}}})

    def locked(self):
        return tool.lock_documents(self.docs, self.profile)

    def test_valid_and_idempotent_lock(self):
        docs = self.locked()
        self.assertEqual(tool.validate_documents(docs, self.profile)['workloads'], 15)
        self.assertEqual(docs, tool.lock_documents(docs, self.profile))

    def test_tag_at_digest_normalization(self):
        item = next(i for i in self.profile['images'] if i['role'] == 'user')
        self.assertEqual(tool.normalize_image(item['source']), tool.locked_image(item))

    def test_unknown_image(self):
        self.docs[0]['spec']['template']['spec']['containers'][0]['image'] = 'unapproved/nf:latest'
        with self.assertRaises(ValueError):
            self.locked()

    def test_missing_nf(self):
        with self.assertRaises(ValueError):
            tool.validate_documents(self.locked()[1:], self.profile)

    def test_duplicate_resource(self):
        docs = self.locked()
        with self.assertRaises(ValueError):
            tool.validate_documents(docs + [docs[0]], self.profile)

    def test_wrong_placement(self):
        docs = self.locked()
        docs[0]['spec']['template']['spec']['nodeSelector'] = self.profile['roles']['ran']
        with self.assertRaises(ValueError):
            tool.validate_documents(docs, self.profile)

    def test_replica_change(self):
        docs = self.locked()
        docs[0]['spec']['replicas'] = 2
        with self.assertRaises(ValueError):
            tool.validate_documents(docs, self.profile)

    def test_multus_rejected(self):
        docs = self.locked()
        docs[0]['spec']['template']['metadata']['annotations'] = {'k8s.v1.cni.cncf.io/networks': 'arbitrary'}
        with self.assertRaises(ValueError):
            tool.validate_documents(docs, self.profile)

    def test_hpa_rejected(self):
        docs = self.locked() + [{'kind': 'HorizontalPodAutoscaler', 'metadata': {'name': 'scale'}}]
        with self.assertRaises(ValueError):
            tool.validate_documents(docs, self.profile)

    def test_role_image_mismatch(self):
        docs = self.locked()
        docs[0]['spec']['template']['spec']['containers'][0]['image'] = tool.locked_image(self.profile['images'][-1])
        with self.assertRaises(ValueError):
            tool.validate_documents(docs, self.profile)

    def test_changed_input_rejected(self):
        with patch.object(tool, 'sha_lf', return_value='0' * 64), self.assertRaises(ValueError):
            tool.load_profile()

    def test_path_escape_rejected(self):
        with self.assertRaises(ValueError):
            tool.repo_path('../outside')

    def test_apply_requires_explicit_disruption_flag(self):
        with patch.object(tool, 'run') as run, self.assertRaises(ValueError):
            tool.apply(SimpleNamespace(confirm_disruption=False), self.profile, ROOT, ROOT)
        run.assert_not_called()

    def test_null_annotations_and_init_containers(self):
        self.docs[0]['spec']['template']['metadata']['annotations'] = None
        self.docs[0]['spec']['template']['spec']['initContainers'] = None
        self.assertEqual(tool.validate_documents(self.locked(), self.profile)['workloads'], 15)

    def test_missing_application_container(self):
        docs = self.locked()
        docs[0]['spec']['template']['spec']['containers'] = None
        with self.assertRaises(ValueError):
            tool.validate_documents(docs, self.profile)

    def test_wrong_namespace(self):
        docs = self.locked()
        docs[0]['metadata']['namespace'] = 'other'
        with self.assertRaises(ValueError):
            tool.validate_documents(docs, self.profile)

    def nodes(self):
        nodes = []
        for role, labels in self.profile['roles'].items():
            nodes.append({'metadata': {'name': role, 'labels': dict(labels, **{
                'kubernetes.io/os': 'linux', 'kubernetes.io/arch': 'amd64'})},
                'spec': {}, 'status': {'conditions': [{'type': 'Ready', 'status': 'True'}],
                'addresses': [{'type': 'InternalIP', 'address': '192.0.2.1'}]}})
        return nodes

    def test_three_distinct_amd64_nodes(self):
        with patch.object(tool, 'run', side_effect=[json.dumps({'items': self.nodes()}), '{}']):
            self.assertEqual(len(tool.cluster_check(self.profile)['roles']), 3)

    def test_wrong_architecture_rejected(self):
        nodes = self.nodes()
        nodes[0]['metadata']['labels']['kubernetes.io/arch'] = 'arm64'
        with patch.object(tool, 'run', return_value=json.dumps({'items': nodes})), self.assertRaises(ValueError):
            tool.cluster_check(self.profile)

    def test_shared_role_node_rejected(self):
        nodes = self.nodes()
        nodes[0]['metadata']['labels'].update(self.profile['roles']['ran'])
        with patch.object(tool, 'run', return_value=json.dumps({'items': nodes[:2]})), self.assertRaises(ValueError):
            tool.cluster_check(self.profile)

    def test_compare_reports_actual_lock_state(self):
        documents = self.locked()
        with patch.object(tool, 'helm_binary', return_value='helm'), patch.object(tool, 'run', return_value=tool.yaml.safe_dump_all(documents)):
            self.assertTrue(tool.compare_current(self.profile, documents)['newDigestTemplateDeployed'])
        with patch.object(tool, 'helm_binary', return_value='helm'), patch.object(tool, 'run', return_value=tool.yaml.safe_dump_all(self.docs)):
            self.assertFalse(tool.compare_current(self.profile, documents)['newDigestTemplateDeployed'])


@unittest.skipUnless(os.environ.get('F5GC_INTEGRATION_CHART_SOURCE'), 'requires Linux Helm and Chart Git source')
class HelmIntegrationTests(unittest.TestCase):
    def test_clean_clone_repeat_render_and_real_post_renderer(self):
        profile = tool.load_profile()
        out = Path(os.environ['F5GC_INTEGRATION_OUT']).resolve()
        out.mkdir(parents=True, exist_ok=True)
        script = ROOT / 'scripts/free5gc-profile.py'
        args = SimpleNamespace(chart_source=os.environ['F5GC_INTEGRATION_CHART_SOURCE'],
                               chart_dir=str(out / 'chart'), out=str(out / 'first'))
        chart, _, _, first = tool.render(args, profile)
        args.out = str(out / 'second')
        _, _, _, second = tool.render(args, profile)
        self.assertEqual([r['semanticSha256'] for r in first['releases']],
                         [r['semanticSha256'] for r in second['releases']])
        for release, expected in zip(profile['releases'], first['releases']):
            values = [p for f in release['values'] for p in ('-f', tool.repo_path(f))]
            raw = tool.run([tool.helm_binary(), 'template', release['name'], chart / release['chartPath'],
                            '-n', profile['namespace'], '--is-upgrade', '--skip-tests', *values,
                            '--post-renderer', script, '--post-renderer-args', 'post-render'])
            docs = tool.parse_documents(raw)
            tool.validate_documents(docs, profile, complete=False)
            digest = hashlib.sha256(json.dumps(docs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            self.assertEqual(digest, expected['semanticSha256'])
        tool.save_json(out / 'integration-report.json', {
            'cleanCloneAndPatch': True, 'repeatRenderIdentical': True, 'realHelmPostRendererIdentical': True,
            'chartCommit': profile['chart']['commit'], 'chartTreeSha256LF': tool.chart_hash(chart),
            'releases': [{'name': r['name'], 'semanticSha256': r['semanticSha256']} for r in first['releases']],
            'resources': first['resources'], 'workloads': first['workloads'], 'applied': False})


if __name__ == '__main__':
    unittest.main()
