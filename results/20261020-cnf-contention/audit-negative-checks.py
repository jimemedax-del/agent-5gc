"""Reject damaged copies of C1 evidence; never edit the formal original."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FORMAL = HERE / 'measure-01'
source = ROOT / 'scripts/audit-cnf-contention.py'
loader = importlib.util.spec_from_file_location('contention_audit', source)
audit = importlib.util.module_from_spec(loader)
loader.loader.exec_module(audit)


def modify(path, function):
    value = audit.read(path)
    function(value)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def replace_tc(path, section, function):
    def change(value):
        value['raw'][section] = function(value['raw'][section])
        value.update(audit.parse_tc_native(json.loads(value['raw']['qdisc']), value['raw']['class'], value['raw']['filter']))
    modify(path, change)


def wrong_source(root):
    modify(root / 'metadata.json', lambda v: v['sha256LF'].__setitem__('scripts/cnf-contention.py', '0' * 64))


def wrong_binding(root):
    modify(root / '01-guarantee-ue1/ue1/iperf.json', lambda v: v['start']['connected'][0].__setitem__('local_host', '10.60.255.254'))


def serial_flows(root):
    modify(root / '01-shared/ue2/iperf.json', lambda v: v['start']['timestamp'].__setitem__('timesecs', v['start']['timestamp']['timesecs'] + 60))


def wrong_capacity(root):
    replace_tc(root / '01-shared/tc-after.json', 'class', lambda text: text.replace('rate 10Mbit', 'rate 1Mbit', 1))


def wrong_classifier(root):
    replace_tc(root / '01-guarantee-ue1/tc-after.json', 'filter', lambda text: text.replace('00280000/00fc0000', '00500000/00fc0000'))


def inactive_bottleneck(root):
    def change(text):
        value = json.loads(text)
        next(q for q in value if q.get('root'))['overlimits'] = 0
        return json.dumps(value)
    replace_tc(root / '01-shared/tc-end.json', 'qdisc', change)


def wrong_cookie(root):
    cookie = audit.read(root / '01-equal/ue1/iperf.json')['start']['cookie']
    with (root / '01-equal/ue2-server.txt').open('a', encoding='utf-8') as stream:
        stream.write('\nCookie: ' + cookie + '\n')


def receiver_byte_mismatch(root):
    modify(root / '01-shared/ue1/iperf.json', lambda v: v['intervals'][10]['sum'].__setitem__('bytes', v['intervals'][10]['sum']['bytes'] + 1200))


def reported_metric(root):
    modify(root / 'groups.json', lambda v: v[1]['users']['ue1'].__setitem__('steadyMbps', 8))


def rtt_bypass(root):
    def change(value):
        value[value.index('-I') + 1] = 'eth0'
    modify(root / '01-equal/ue1/ping-command.json', change)


def native_policer(root):
    (root / '01-priority-ue1/qos-before.txt').write_text('QoS Enable: 1\n')


def admission_modified_queue(root):
    modify(root / 'admission-rejection.json', lambda v: v['after']['class'][0]['options'].__setitem__('rate', 1))


def changed_smf(root):
    def change(value):
        name = next(n for n in value['free5gc'] if n.startswith('free5gc-helm-free5gc-smf-smf-'))
        value['free5gc'][name]['uid'] = 'replacement-smf'
    modify(root / 'final/pods.json', change)


def missing_round(root):
    modify(root / 'groups.json', lambda v: v.pop())


def weak_contract(root):
    modify(root / 'experiment.json', lambda v: v['acceptance'].__setitem__('guaranteedHighMinimumMbps', .1))


def not_restored(root):
    modify(root / 'restore/tc-after.json', lambda v: v['qdisc'][0].__setitem__('kind', 'htb'))


if __name__ == '__main__':
    control = audit.audit(FORMAL)
    assert control['networkAcceptance']['passed'], 'Do not label a failed network control as accepted'
    before = {p.relative_to(FORMAL).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in FORMAL.rglob('*') if p.is_file()}
    cases = []
    for name, mutation in [('source-hash', wrong_source), ('wrong-ue', wrong_binding), ('serial-flows', serial_flows),
                           ('wrong-capacity', wrong_capacity), ('wrong-classifier', wrong_classifier),
                           ('inactive-bottleneck', inactive_bottleneck), ('wrong-backend-cookie', wrong_cookie),
                           ('receiver-byte-mismatch', receiver_byte_mismatch), ('reported-metric', reported_metric),
                           ('rtt-bypass', rtt_bypass), ('native-policer-enabled', native_policer),
                           ('admission-changed-queue', admission_modified_queue), ('smf-replacement', changed_smf),
                           ('missing-round', missing_round), ('weakened-contract', weak_contract), ('not-restored', not_restored)]:
        workspace = (ROOT / '.work').resolve()
        parent = Path(tempfile.mkdtemp(prefix='cnf-c-negative-', dir=workspace)).resolve()
        assert parent.is_relative_to(workspace)
        target = parent / 'formal'
        shutil.copytree(FORMAL, target)
        mutation(target)
        try:
            audit.audit(target)
        except ValueError as exc:
            cases.append({'case': name, 'outcome': 'rejected', 'reason': str(exc)})
        else:
            raise RuntimeError('Damaged evidence accepted: ' + name)
    after = {p.relative_to(FORMAL).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in FORMAL.rglob('*') if p.is_file()}
    assert before == after
    result = {'outcome': 'passed', 'uncorruptedControlPassed': True, 'networkControlAccepted': True,
              'originalEvidenceUnchanged': True,
              'auditorSha256LF': hashlib.sha256(source.read_bytes().replace(b'\r\n', b'\n')).hexdigest(), 'cases': cases}
    with (HERE / 'audit-negative-checks.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
