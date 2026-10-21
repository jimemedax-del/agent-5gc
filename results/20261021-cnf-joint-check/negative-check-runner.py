import importlib.util
import json
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / 'results/20261021-cnf-joint-check/run-01'
loader = importlib.util.spec_from_file_location('negative_audit', ROOT / 'scripts/audit-cnf-joint-check.py')
audit = importlib.util.module_from_spec(loader)
loader.loader.exec_module(audit)


def change(folder, name, edit):
    file = folder / name
    value = json.loads(file.read_text(encoding='utf-8'))
    edit(value)
    file.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def wrong_log(folder):
    file = folder / 'B-shaped/01-core/envoy-access.txt'
    file.write_text(file.read_text(encoding='utf-8').replace('m2_core', 'm2_edge'), encoding='utf-8')


def pod_drift(folder):
    def edit(value):
        next(iter(value['free5gc'].values()))['uid'] = 'wrong-uid'
    change(folder, 'B-shaped/01-core/guard-pods.json', edit)


def source_tamper(folder):
    file = folder / 'inputs/scripts/cnf-joint-check.py'
    file.write_text(file.read_text(encoding='utf-8') + '\n# changed\n', encoding='utf-8')


cases = {
    'receiver-bytes': lambda f: change(f, 'B-shaped/01-core/iperf.json',
         lambda x: x['end']['sum_received'].__setitem__('bytes', 1)),
    'wrong-proxy-route': wrong_log,
    'wrong-endpoint-uid': lambda f: change(f, 'B-shaped/before/edge-endpoints.json',
         lambda x: x['subsets'][0]['addresses'][0]['targetRef'].__setitem__('uid', 'wrong-uid')),
    'pod-identity-drift': pod_drift,
    'wrong-tbf-rate': lambda f: change(f, 'B-shaped/01-core/qdisc-before.json',
         lambda x: x[0]['options'].__setitem__('rate', 1)),
    'phase-median': lambda f: change(f, 'B-shaped/summary.json',
         lambda x: x['medianReceiverMbps'].__setitem__('core', 5)),
    'final-native-qos': lambda f: change(f, 'final/observed/observation.json',
         lambda x: x.__setitem__('nativeQos', 'QoS Enable: 1')),
    'missing-policy-evidence': lambda f: (f / 'B-shaped/01-core/after-policies.json').unlink(),
    'frozen-source-changed': source_tamper,
    'threshold-relaxed': lambda f: change(f, 'experiment.json',
         lambda x: x['acceptance'].__setitem__('unshapedMinimumMedianMbps', 1)),
    'restoration-failed': lambda f: change(f, 'summary.json',
         lambda x: x.__setitem__('restoration', 'failed')),
    'missing-session': lambda f: change(f, 'B-shaped/rounds.json', lambda x: x.pop()),
}
records = []
for name, edit in cases.items():
    with tempfile.TemporaryDirectory(prefix='joint-negative-', dir=ROOT / '.work') as directory:
        base = Path(directory).resolve()
        assert base.is_relative_to((ROOT / '.work').resolve())
        copy = base / 'evidence'
        shutil.copytree(FOLDER, copy)
        edit(copy)
        try:
            audit.audit(copy, ROOT)
        except (ValueError, RuntimeError, KeyError, FileNotFoundError, StopIteration) as error:
            records.append({'case': name, 'outcome': 'rejected_as_expected', 'reason': str(error)})
        else:
            raise RuntimeError('Corrupt evidence accepted: ' + name)
result = {'outcome': 'passed', 'cases': records, 'acceptedOriginalUnmodified': True}
path = ROOT / 'results/20261021-cnf-joint-check/negative-audit-checks.json'
path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'outcome': 'passed', 'rejectedCases': len(records)}))
