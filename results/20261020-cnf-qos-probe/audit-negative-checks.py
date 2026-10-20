"""Corrupt temporary copies of formal B evidence; original files remain intact."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FORMAL = HERE / 'probe-04'
source = ROOT / 'scripts/audit-cnf-qos-probe.py'
spec = importlib.util.spec_from_file_location('qos_audit', source)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def modify(path, callback):
    value = audit.read(path)
    callback(value)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def source_hash(root):
    modify(root / 'metadata.json', lambda v: v['sha256LF'].__setitem__('scripts/cnf-qos-probe.py', '0' * 64))


def wrong_ue(root):
    modify(root / 'cap-on-01-uplink-ue2/ue2/iperf.json',
           lambda v: v['start']['connected'][0].__setitem__('local_host', '10.60.255.254'))


def wrong_qer(root):
    def change(value):
        for rule in value['rules']['qer']:
            if rule['MBR']['UL_Kbps'] == 2000:
                rule['SEID'] += 100
    modify(root / 'cap-on/inspection.json', change)


def wrong_switch(root):
    modify(root / 'cap-on-01-uplink-ue2/control.json', lambda v: v.__setitem__('qosEnable', 0))


def reported_rate(root):
    modify(root / 'flows.json', lambda v: v[0].__setitem__('receivedMbps', .01))


def unrestored(root):
    modify(root / 'final/inspection.json', lambda v: v['subscription']['dnnConfigurations']['internet']
           .__setitem__('sessionAmbr', {'uplink': '2 Mbps', 'downlink': '4 Mbps'}))


def duplicate_session(root):
    modify(root / 'cap-on/inspection.json', lambda v: v.__setitem__('ue2Sessions', v['ue2Sessions'] + v['ue2Sessions']))


def replacement_smf(root):
    def change(value):
        name = next(n for n in value['pods']['free5gc'] if n.startswith('free5gc-helm-free5gc-smf-smf-'))
        value['pods']['free5gc'][name]['uid'] = 'replacement'
    modify(root / 'final/inspection.json', change)


def wrong_backend(root):
    cookie = audit.read(root / 'cap-on-01-uplink-ue2/ue2/iperf.json')['start']['cookie']
    with (root / 'cap-on-01-uplink-ue2/cnf-a-core-ue1.txt').open('a', encoding='utf-8') as stream:
        stream.write('\n' + cookie + '\n')


def missing_round(root):
    modify(root / 'flows.json', lambda v: v.pop())


def missing_source_hashes(root):
    modify(root / 'metadata.json', lambda v: v.__setitem__('sha256LF', {}))


def premature_reset(root):
    path = root / 'cap-off-01-uplink-ue2/envoy-access.txt'
    values = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    changed = False
    for value in values:
        if value['test_user'] == 'ue2' and value['flags'] in ['UR', 'DC']:
            value['duration_ms'] = 5000
            changed = True
    assert changed, 'This negative case requires an observed payload-stop flag'
    path.write_text('\n'.join(json.dumps(v) for v in values) + '\n', encoding='utf-8')


if __name__ == '__main__':
    audit.audit(FORMAL)
    before = {p.relative_to(FORMAL).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in FORMAL.rglob('*') if p.is_file()}
    cases = []
    for name, mutation in [('source-hash', source_hash), ('wrong-ue', wrong_ue), ('cross-session-qer', wrong_qer),
                           ('wrong-switch', wrong_switch), ('reported-rate', reported_rate), ('unrestored', unrestored),
                           ('duplicate-session', duplicate_session), ('smf-replacement', replacement_smf),
                           ('cookie-at-wrong-backend', wrong_backend), ('missing-round', missing_round),
                           ('missing-source-hashes', missing_source_hashes), ('premature-reset', premature_reset)]:
        workspace = (ROOT / '.work').resolve()
        parent = Path(tempfile.mkdtemp(prefix='cnf-b-negative-', dir=workspace)).resolve()
        assert parent.is_relative_to(workspace)
        target = parent / 'formal'
        shutil.copytree(FORMAL, target)
        mutation(target)
        try:
            audit.audit(target)
        except ValueError as exc:
            cases.append({'case': name, 'outcome': 'rejected', 'reason': str(exc)})
        else:
            raise RuntimeError('Corrupted evidence accepted: ' + name)
    after = {p.relative_to(FORMAL).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in FORMAL.rglob('*') if p.is_file()}
    assert before == after
    result = {'outcome': 'passed', 'uncorruptedControlPassed': True, 'originalEvidenceUnchanged': True,
              'auditorSha256LF': hashlib.sha256(source.read_bytes().replace(b'\r\n', b'\n')).hexdigest(), 'cases': cases}
    with (HERE / 'audit-negative-checks.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
