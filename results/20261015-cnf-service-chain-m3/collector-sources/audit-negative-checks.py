import importlib.util
import json
from pathlib import Path
import shutil

root = Path.cwd()
module = importlib.util.spec_from_file_location('audit', root / 'scripts/audit-cnf-m3.py')
audit = importlib.util.module_from_spec(module)
module.loader.exec_module(audit)
original = root / 'results/20261015-cnf-service-chain-m3'
fixture = root / '.work/m3-audit-negative-01'
shutil.copytree(original, fixture)
checks = []

def rejects(name, relative, mutate, expected):
    path = fixture / relative
    raw = path.read_bytes()
    try:
        data = json.loads(raw)
        mutate(data)
        path.write_text(json.dumps(data), encoding='utf-8')
        try:
            audit.audit(fixture, root)
        except ValueError as exc:
            if expected not in str(exc):
                raise
            checks.append({'case': name, 'outcome': 'rejected-as-required', 'reason': str(exc)})
        else:
            raise RuntimeError('Corrupted evidence was accepted: ' + name)
    finally:
        path.write_bytes(raw)

rejects('zero receiver payload', 'measure-01/01-core/iperf.json',
        lambda d: d['end']['sum_received'].update(bytes=0), 'Incomplete receiver')
rejects('stale shaping rate', 'measure-01/01-core/qdisc-after.json',
        lambda d: d[0]['options'].update(rate=1875000), 'Wrong TBF rate')
rejects('guard Pod replacement', 'measure-01/01-core/guard-pods.json',
        lambda d: next(iter(d['free5gc'].values())).update(uid='changed'), 'identity drift')
rejects('summary ratio changed', 'measure-01/summary.json',
        lambda d: d['evidence'].update(medianRatio=10), 'Summary ratio differs')
rejects('truncated memory sampling', 'measure-01/01-core/resource-samples.json',
        lambda d: d.clear(), 'Missing/unsafe memory')
audit.audit(fixture, root)
with (original / 'audit-negative-checks.json').open('x', encoding='utf-8') as stream:
    json.dump({'outcome': 'passed', 'cases': checks, 'originalEvidenceModified': False}, stream, indent=2)
    stream.write('\n')
print(json.dumps(checks, indent=2))
