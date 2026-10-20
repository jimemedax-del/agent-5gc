"""Reject corrupted copies of A evidence; never mutate the original batch."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(__file__).resolve().parent
source = ROOT / 'scripts/audit-cnf-multi-ue.py'
module = importlib.util.spec_from_file_location('audit', source)
audit = importlib.util.module_from_spec(module)
module.loader.exec_module(audit)
ACTIONS = {name: name + '-01' for name in ['deploy', 'individual', 'smoke', 'concurrent']}
ACTIONS['check'] = 'final-check-01'


def modify_json(path, function):
    value = audit.read(path)
    function(value)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def wrong_source(folder):
    modify_json(folder / 'concurrent-01/metadata.json',
                lambda v: v['sha256LF'].__setitem__('scripts/cnf-multi-ue.py', '0' * 64))


def wrong_ue(folder):
    modify_json(folder / 'concurrent-01/01-uplink-core/ue2/iperf.json',
                lambda v: v['start']['connected'][0].__setitem__('local_host', '10.60.255.254'))


def wrong_backend(folder):
    flow = folder / 'concurrent-01/01-uplink-core'
    cookie = audit.read(flow / 'ue1/iperf.json')['start']['cookie']
    with (flow / 'cnf-a-core-ue2.txt').open('a', encoding='utf-8') as stream:
        stream.write('\n' + cookie + '\n')


def serial_flow(folder):
    modify_json(folder / 'concurrent-01/01-uplink-core/ue2/iperf.json',
                lambda v: v['start']['timestamp'].__setitem__('timesecs', v['start']['timestamp']['timesecs'] + 30))


def changed_smf(folder):
    def change(value):
        name = next(n for n in value['free5gc'] if n.startswith('free5gc-helm-free5gc-smf-smf-'))
        value['free5gc'][name]['uid'] = 'replacement-smf'
    modify_json(folder / 'final-check-01/live/pods.json', change)


def changed_policy(folder):
    def change(value):
        policy = next(v for v in value if v['kind'] == 'NetworkPolicy')
        policy['body']['ingress'] = [{}]
    modify_json(folder / 'final-check-01/live/resources.json', change)


if __name__ == '__main__':
    audit.audit(EVIDENCE, ACTIONS)
    original_hashes = {p.relative_to(EVIDENCE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in EVIDENCE.rglob('*') if p.is_file()}
    cases = []
    for name, mutation in [('source-hash', wrong_source), ('wrong-ue-binding', wrong_ue),
                           ('cookie-at-wrong-backend', wrong_backend), ('serial-traffic', serial_flow),
                           ('smf-replacement', changed_smf), ('policy-bypass', changed_policy)]:
        parent = Path(tempfile.mkdtemp(prefix='cnf-a-negative-', dir=ROOT / '.work')).resolve()
        assert parent.is_relative_to((ROOT / '.work').resolve())
        copy = parent / 'evidence'
        shutil.copytree(EVIDENCE, copy)
        mutation(copy)
        try:
            audit.audit(copy, ACTIONS)
        except ValueError as exc:
            cases.append({'case': name, 'outcome': 'rejected', 'reason': str(exc)})
        else:
            raise RuntimeError('Corrupted evidence accepted: ' + name)
    after_hashes = {p.relative_to(EVIDENCE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in EVIDENCE.rglob('*') if p.is_file()}
    assert original_hashes == after_hashes, 'Original evidence changed during negative checks'
    result = {'outcome': 'passed', 'uncorruptedControlPassed': True, 'originalEvidenceUnchanged': True,
              'auditorSha256LF': hashlib.sha256(source.read_bytes().replace(b'\r\n', b'\n')).hexdigest(), 'cases': cases}
    with (EVIDENCE / 'audit-negative-checks.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
