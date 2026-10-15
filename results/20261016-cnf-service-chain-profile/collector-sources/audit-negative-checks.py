"""Corrupt an isolated copy to test independent R2.S3 audit rejection paths."""
import importlib.util
import json
from pathlib import Path
import shutil

root = Path.cwd()
module = importlib.util.spec_from_file_location('audit', root / 'scripts/audit-cnf-profile.py')
audit = importlib.util.module_from_spec(module)
module.loader.exec_module(audit)
original = root / 'results/20261016-cnf-service-chain-profile'
fixture = root / '.work/profile-audit-negative-01'
shutil.copytree(original, fixture)
checks = []

def rejects(name, relative, change, reason):
    path = fixture / relative
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
        change(value)
        path.write_text(json.dumps(value), encoding='utf-8')
        try:
            audit.audit(fixture, root)
        except ValueError as exc:
            if reason not in str(exc):
                raise
            checks.append({'case': name, 'outcome': 'rejected-as-required', 'reason': str(exc)})
        else:
            raise RuntimeError('Corrupt evidence accepted: ' + name)
    finally:
        path.write_bytes(raw)

rejects('changed restore snapshot hash', 'qos-apply-01/snapshot.json',
        lambda d: d.update(manifestSha256='changed'), 'Invalid restore snapshot')
rejects('Service replaced during rebuild', 'rebuild-01/live-applied/resources.json',
        lambda d: next(r for r in d if r['kind'] == 'Service').update(uid='changed'), 'Rebuild changed Services')
rejects('successful backend bypass', 'protected-measure-02/measurement/isolation/01/core-pod-cnf-netpol-probe-core.json',
        lambda d: d.update(returnCode=0), 'Raw allow/deny mismatch')
rejects('wrong QoS rate', 'qos-measure-01/measurement/01-core/qdisc-after.json',
        lambda d: d[0]['options'].update(rate=1875000), 'QoS counter/rate proof')
rejects('5GC replacement during QoS test', 'qos-measure-01/measurement/01-core/guard-pods.json',
        lambda d: next(iter(d['free5gc'].values())).update(uid='replaced'), '5GC identity changed')
audit.audit(fixture, root)
with (original / 'audit-negative-checks.json').open('x', encoding='utf-8') as stream:
    json.dump({'outcome': 'passed', 'cases': checks, 'originalEvidenceModified': False}, stream, indent=2)
    stream.write('\n')
print(json.dumps(checks, indent=2))
