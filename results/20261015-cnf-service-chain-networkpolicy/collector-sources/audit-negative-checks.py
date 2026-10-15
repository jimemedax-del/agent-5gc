"""Exercise rejection paths on an isolated evidence copy; do not alter original evidence."""
import importlib.util
import json
from pathlib import Path
import shutil

root = Path.cwd()
module = importlib.util.spec_from_file_location('audit', root / 'scripts/audit-cnf-networkpolicy.py')
audit = importlib.util.module_from_spec(module)
module.loader.exec_module(audit)
original = root / 'results/20261015-cnf-service-chain-networkpolicy'
fixture = root / '.work/netpol-audit-negative-01'
shutil.copytree(original, fixture)
checks = []

def rejects(name, relative, change, reason):
    path = fixture / 'run-01' / relative
    raw = path.read_bytes()
    try:
        data = json.loads(raw)
        change(data)
        path.write_text(json.dumps(data), encoding='utf-8')
        try:
            audit.audit(fixture / 'run-01', root)
        except ValueError as exc:
            if reason not in str(exc):
                raise
            checks.append({'case': name, 'outcome': 'rejected-as-required', 'reason': str(exc)})
        else:
            raise RuntimeError('Corrupt evidence accepted: ' + name)
    finally:
        path.write_bytes(raw)

target = 'isolated-final/01/core-pod-cnf-netpol-probe-core.json'
rejects('successful backend bypass', target, lambda d: d.update(returnCode=0), 'Raw connection')
rejects('kubectl error mistaken for denial', target,
        lambda d: d.update(returnCode=1, stderr='error: pod not found'), 'Raw connection')
rejects('unreachable destination in open control', 'open-after-removal/01/core-pod-cnf-netpol-probe-core.json',
        lambda d: d.update(returnCode=124), 'Raw connection')
rejects('Pod replacement during UE traffic', 'business/01-uplink-core/guard-pods.json',
        lambda d: next(iter(d['free5gc'].values())).update(uid='replaced'), 'Guard identity')
audit.audit(fixture / 'run-01', root)
with (original / 'audit-negative-checks.json').open('x', encoding='utf-8') as stream:
    json.dump({'outcome': 'passed', 'cases': checks, 'originalEvidenceModified': False}, stream, indent=2)
    stream.write('\n')
print(json.dumps(checks, indent=2))
