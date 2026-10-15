"""One-time corruption checks against an isolated copy of the saved M4 evidence."""
import importlib.util
import json
from pathlib import Path
import shutil

root = Path.cwd()
module = importlib.util.spec_from_file_location('audit', root / 'scripts/audit-cnf-m4.py')
audit = importlib.util.module_from_spec(module)
module.loader.exec_module(audit)
original = root / 'results/20261015-cnf-service-chain-m4'
fixture = root / '.work/m4-audit-negative-01'
shutil.copytree(original, fixture)
checks = []

def rejects(name, relative, change, expected):
    path = fixture / relative
    raw = path.read_bytes()
    try:
        data = json.loads(raw)
        change(data)
        path.write_text(json.dumps(data), encoding='utf-8')
        try:
            audit.audit(fixture, 'measure-01', root)
        except ValueError as exc:
            if expected not in str(exc):
                raise
            checks.append({'case': name, 'outcome': 'rejected-as-required', 'reason': str(exc)})
        else:
            raise RuntimeError('Corrupt evidence accepted: ' + name)
    finally:
        path.write_bytes(raw)

rejects('stale Pod in endpoint', 'measure-01/live-before/edge-endpoints.json',
        lambda d: d['subsets'][0]['addresses'][0]['targetRef'].update(uid='old-uid'), 'current target Pod')
rejects('remaining TBF', 'measure-01/01-uplink-edge/qdisc-before.json',
        lambda d: d[0].update(kind='tbf'), 'Shaping remains')
rejects('guard Pod replacement', 'measure-01/01-uplink-edge/guard-pods.json',
        lambda d: next(iter(d['free5gc'].values())).update(uid='changed'), 'identity drift')
audit.audit(fixture, 'measure-01', root)
with (original / 'audit-negative-checks.json').open('x', encoding='utf-8') as stream:
    json.dump({'outcome': 'passed', 'cases': checks, 'originalEvidenceModified': False}, stream, indent=2)
    stream.write('\n')
print(json.dumps(checks, indent=2))
