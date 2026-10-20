"""Corrupt bounded copies of C2 evidence, preserving the formal original."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
FORMAL = HERE / 'measure-02'
source = ROOT / 'scripts/audit-cnf-contention-uplink.py'
spec = importlib.util.spec_from_file_location('audit_c2', source)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def modify(path, function):
    value = audit.read(path)
    function(value)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def native(root, section, function):
    def change(value):
        tc = value['tc']
        tc['raw'][section] = function(tc['raw'][section])
        tc.update(audit.parse_tc_native(json.loads(tc['raw']['qdisc']), tc['raw']['class'], tc['raw']['filter']))
    modify(root / '01-guarantee-ue1/network-end.json', change)


def wrong_source(root):
    modify(root / 'metadata.json', lambda v: v['sha256LF'].__setitem__('scripts/cnf-contention-uplink.py', '0' * 64))


def wrong_ue(root):
    modify(root / '01-shared/ue1/iperf.json', lambda v: v['start']['connected'][0].__setitem__('local_host', '10.60.255.254'))


def wrong_direction(root):
    modify(root / '01-shared/ue1/iperf-command.json', lambda v: v.append('-R'))


def missing_receiver(root):
    modify(root / '01-shared/ue1/iperf.json', lambda v: v.pop('server_output_json'))


def receiver_byte_mismatch(root):
    modify(root / '01-equal/ue1/iperf.json', lambda v: v['server_output_json']['intervals'][10]['sum'].__setitem__(
        'bytes', v['server_output_json']['intervals'][10]['sum']['bytes'] + 1200))


def underload(root):
    def change(value):
        for interval in value['intervals']:
            interval['sum'].update(bytes=499200, packets=416)
    modify(root / '01-shared/ue1/iperf.json', change)


def serial_receivers(root):
    modify(root / '01-equal/ue2/iperf.json', lambda v: v['server_output_json']['start']['timestamp'].__setitem__(
        'timesecs', v['server_output_json']['start']['timestamp']['timesecs'] + 60))


def wrong_redirect(root):
    def change(value):
        next(f['options']['actions'][0] for f in value['ingressFilters'] if 'options' in f)['to_dev'] = 'foreign'
    modify(root / '01-equal/network-end.json', change)


def source_port_classifier(root):
    native(root, 'filter', lambda text: text.replace('00001451/0000ffff', '14510000/ffff0000'))


def wrong_capacity(root):
    native(root, 'class', lambda text: text.replace('rate 10Mbit', 'rate 1Mbit', 1))


def inactive_bottleneck(root):
    def change(text):
        value = json.loads(text)
        next(q for q in value if q.get('root'))['overlimits'] = 0
        return json.dumps(value)
    native(root, 'qdisc', change)


def wrong_cookie(root):
    modify(root / '01-equal/ue1/iperf.json', lambda v: v['server_output_json']['start'].__setitem__('cookie', 'other'))


def rtt_bypass(root):
    def change(value):
        value[value.index('-I') + 1] = 'eth0'
    modify(root / '01-equal/ue1/ping-command.json', change)


def native_policer(root):
    (root / '01-shared/qos-before.txt').write_text('QoS Enable: 1\n')


def changed_smf(root):
    def change(value):
        name = next(n for n in value['free5gc'] if n.startswith('free5gc-helm-free5gc-smf-smf-'))
        value['free5gc'][name]['uid'] = 'replacement-smf'
    modify(root / 'final/pods.json', change)


def missing_round(root):
    modify(root / 'groups.json', lambda v: v.pop())


def weak_contract(root):
    modify(root / 'experiment.json', lambda v: v['acceptance'].__setitem__('guaranteedHighMinimumMbps', .1))


def fake_rtt_quantile(root):
    modify(root / 'groups.json', lambda v: v[1]['users']['ue1']['rtt'].__setitem__('p95Ms', 0))


def changed_admission(root):
    modify(root / 'admission-rejection.json', lambda v: v['after']['tc']['class'][0]['options'].__setitem__('rate', 1))


def not_restored(root):
    modify(root / 'restore/after.json', lambda v: v.__setitem__('link', [{'ifname': 'leftover'}]))


if __name__ == '__main__':
    assert audit.audit(FORMAL)['networkAcceptance']['passed'], 'Network control must pass before corruption checks'
    before = {p.relative_to(FORMAL).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in FORMAL.rglob('*') if p.is_file()}
    cases = []
    for name, mutation in [('source-hash', wrong_source), ('wrong-ue', wrong_ue), ('reverse-direction', wrong_direction),
                           ('missing-server-receiver', missing_receiver), ('receiver-byte-mismatch', receiver_byte_mismatch),
                           ('actual-underload', underload), ('serial-receivers', serial_receivers), ('wrong-redirect', wrong_redirect),
                           ('source-port-classifier', source_port_classifier), ('wrong-capacity', wrong_capacity),
                           ('inactive-bottleneck', inactive_bottleneck), ('receiver-cookie', wrong_cookie),
                           ('rtt-bypass', rtt_bypass), ('native-policer-enabled', native_policer), ('smf-replacement', changed_smf),
                           ('missing-round', missing_round), ('weakened-contract', weak_contract), ('fake-rtt-quantile', fake_rtt_quantile),
                           ('admission-changed-queue', changed_admission), ('not-restored', not_restored)]:
        workspace = (ROOT / '.work').resolve()
        parent = Path(tempfile.mkdtemp(prefix='cnf-c2-negative-', dir=workspace)).resolve()
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
    result = {'outcome': 'passed', 'uncorruptedNetworkControlAccepted': True, 'originalEvidenceUnchanged': True,
              'auditorSha256LF': hashlib.sha256(source.read_bytes().replace(b'\r\n', b'\n')).hexdigest(), 'cases': cases}
    with (HERE / 'audit-negative-checks.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
