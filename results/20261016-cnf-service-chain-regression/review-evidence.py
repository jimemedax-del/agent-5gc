"""Review this regression batch using the existing raw-flow auditor."""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BATCH = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('audit', ROOT / 'scripts/audit-cnf-profile.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
read, require = audit.read, audit.require


def review():
    folder = BATCH / 'protected-measure-01'
    for action in ['check-01', 'protected-measure-01']:
        require(read(BATCH / action / 'summary.json')['outcome'] == 'passed', 'Failed action')
    before, after = [read(folder / f'{label}-pods.json') for label in ['before', 'after']]
    require(audit.ids(before) == audit.ids(after) == audit.ids(read(BATCH / 'check-01/live/pods.json')),
            'Original Pod/container identities changed')
    require(len(after['free5gc']) == 15 and len(after['cnf-lab']) == 3 and
            all(c['ready'] and c['restartCount'] == 0 for pods in after.values()
                for pod in pods.values() for c in pod['containers']), 'Readiness/restarts/probe cleanup')
    require(read(folder / 'live-after/state.json')['state'] == 'protected', 'Final state differs')
    policies = [read(folder / 'measurement' / f'policies-{s}-measure.json') for s in ['before', 'after']]
    require(len(policies[0]) == 2 and
            {p['metadata']['name']: (p['metadata']['uid'], p['spec']) for p in policies[0]} ==
            {p['metadata']['name']: (p['metadata']['uid'], p['spec']) for p in policies[1]}, 'Policy changed')
    resources = [read(folder / f'live-{s}/resources.json') for s in ['before', 'after']]
    services = [{r['name']: (r['uid'], r['body']['clusterIP'], r['body']['ports'])
                 for r in items if r['kind'] == 'Service'} for items in resources]
    require(services[0] == services[1] and len(services[0]) == 3, 'Services changed')
    routing = read(ROOT / 'infra/cnfs/m2-experiment.json')
    runtime = {r['name']: {'spec': r['body']} for r in resources[0] if r['kind'] == 'Service'}
    state = read(folder / 'measurement/measurement-pods.json')
    envoy = audit.one(before, 'cnf-m1-envoy')
    rows = read(folder / 'measurement/business/rounds.json')
    counts = Counter(r['businessClass'] + '/' + r['direction'] for r in rows)
    require(dict(counts) == {f'{name}/{direction}': 5 for name in ['core', 'edge']
                             for direction in ['uplink', 'downlink']}, 'Incomplete business batch')
    cookies, samples, payload = set(), [], 0
    for row in rows:
        run = folder / 'measurement/business' / f'{row["round"]:02d}-{row["direction"]}-{row["businessClass"]}'
        require(read(run / 'summary.json') == row, 'Per-round summary differs')
        cookie, received, _, memory = audit.raw_flow(run, row, routing, runtime, envoy['ip'], row['direction'], state)
        require(cookie not in cookies, 'Reused cookie')
        cookies.add(cookie)
        samples.extend(memory)
        payload += received
    isolation = folder / 'measurement/isolation'
    checks = read(isolation / 'connections.json')
    require(len(checks) == 45 and sum(r['expectedAllowed'] for r in checks) == 15, 'Isolation counts differ')
    for number in range(1, 4):
        raw = [read(p) for p in (isolation / f'{number:02d}').glob('*.json')]
        tcp = [r for r in raw if 'sourcePod' in r]
        require(len(tcp) == 15, 'Missing raw TCP checks')
        for item in tcp:
            code = item['returnCode']
            require(code == 0 if item['expectedAllowed'] else code == 124 or code == 1 and
                    any(t in item['stderr'] for t in ['Connection refused', 'Connection timed out',
                        'No route to host', 'Network is unreachable']), 'Raw isolation failure')
        for name in ['core', 'edge']:
            dns = read(isolation / f'{number:02d}/dns-{name}.json')
            service = next(r['backendService'] for r in routing['classes'] if r['name'] == name)
            require(dns['returnCode'] == 0 and {s.split()[0] for s in dns['stdout'].splitlines()} ==
                    {runtime[service]['spec']['clusterIP']}, 'DNS differs')
    hashes = 0
    for action in ['check-01', 'protected-measure-01']:
        for relative, expected in read(BATCH / action / 'metadata.json')['sha256LF'].items():
            require(hashlib.sha256((ROOT / relative).read_bytes().replace(b'\r\n', b'\n')).hexdigest() == expected,
                    'Source hash differs: ' + relative)
            hashes += 1
    return {'outcome': 'passed', 'stage': 'R2.S3', 'scope': 'protected regression; not R2.S4 acceptance',
            'sessions': len(rows), 'uniqueCookies': len(cookies), 'proxyConnections': 2 * len(cookies),
            'counts': dict(counts), 'allowedChecks': 15, 'deniedChecks': 30, 'dnsChecks': 6,
            'totalReceivedBytes': payload, 'sourceHashChecks': hashes, 'originalPodsUnchanged': 18,
            'finalState': 'protected', 'finalContainerRestarts': 0, 'probePodsRemoved': True,
            'upfMemorySamples': len(samples),
            'sampledPeakUpfMiB': max(s['upfMemoryBytes'] for s in samples) / 1024 ** 2}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    result = review()
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
