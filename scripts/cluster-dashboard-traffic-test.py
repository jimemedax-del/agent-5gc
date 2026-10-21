#!/usr/bin/env python3
"""Bounded two-UE mixed TCP experiment; no deployment or policy mutations."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.request import urlopen


PHASES = [
    {'id': 'A', 'flows': [('ue1', 'core', 'ul'), ('ue2', 'edge', 'dl')]},
    {'id': 'B', 'flows': [('ue1', 'core', 'ul'), ('ue2', 'edge', 'dl'),
                          ('ue1', 'edge', 'dl'), ('ue2', 'core', 'ul')]},
    {'id': 'C', 'flows': [('ue2', 'core', 'dl'), ('ue2', 'edge', 'ul')]},
]
PORTS = {'ue1/core': [31301, 15301], 'ue2/core': [31302, 15302],
         'ue1/edge': [31303, 15303], 'ue2/edge': [31304, 15304]}

REMOTE = r'''
import concurrent.futures, datetime as dt, fcntl, json, os, subprocess, time
CONFIG = __CONFIG__
os.environ['KUBECONFIG'] = os.path.expanduser('~/.kube/config')
def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def emit(event): print(json.dumps(event), flush=True)
def get(args):
    return json.loads(subprocess.check_output(['kubectl', '--request-timeout=8s', *args, '-o', 'json'], text=True, timeout=12))
def snapshot():
    pods = get(['get', 'pods', '-A'])['items']
    return [{'namespace':p['metadata']['namespace'], 'name':p['metadata']['name'],
        'uid':p['metadata']['uid'], 'ready':any(c['type']=='Ready' and c['status']=='True' for c in p['status'].get('conditions', [])),
        'restarts':sum(c.get('restartCount',0) for c in p['status'].get('containerStatuses', []) + p['status'].get('initContainerStatuses', []))} for p in pods]
lock = open(os.path.expanduser('~/.work/cnf-planner-live.lock'), 'a')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
before = snapshot()
emit({'event':'before', 'at':now(), 'pods':before})
assert all(p['ready'] for p in before), 'Unready Pod at experiment start'
ues = {}
for user, prefix in [('ue1','ueransim-ue-'), ('ue2','cnf-a-ue2-')]:
    found = [p for p in before if p['namespace']=='free5gc' and p['name'].startswith(prefix)]
    assert len(found)==1
    pod = found[0]
    interface = json.loads(subprocess.check_output(['kubectl','-n','free5gc','exec',pod['name'],'--','ip','-j','-4','addr','show','uesimtun0'],text=True,timeout=12))
    addresses = [a['local'] for i in interface for a in i.get('addr_info',[]) if a['family']=='inet']
    assert len(addresses)==1
    ues[user] = dict(pod, ip=addresses[0])
emit({'event':'ues', 'at':now(), 'ues':ues})
def flow(spec):
    user, backend, direction = spec
    port = CONFIG['ports'][user+'/'+backend][0]
    args = ['kubectl','-n','free5gc','exec',ues[user]['name'],'--','iperf3',
        '-c','192.168.244.128','-p',str(port),'-B',ues[user]['ip'],
        '-b','1M','-t','45','-l','16384','-P','1','-J']
    if direction=='dl': args += ['-R']
    start = now()
    result = subprocess.run(args,capture_output=True,text=True,timeout=58)
    try: iperf = json.loads(result.stdout)
    except ValueError: iperf = {'error': 'Invalid iperf JSON', 'stdout': result.stdout[-1000:]}
    return {'user':user, 'backend':backend, 'direction':direction, 'startedAt':start,
        'finishedAt':now(), 'command':args, 'returncode':result.returncode, 'stderr':result.stderr, 'iperf':iperf}
for phase in CONFIG['phases']:
    start = now()
    emit({'event':'phase-start','phase':phase['id'],'at':start,'flows':phase['flows']})
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(flow, phase['flows']))
    emit({'event':'phase-end','phase':phase['id'],'startedAt':start,'at':now(),'flows':results})
    assert all(r['returncode']==0 and 'error' not in r['iperf'] for r in results), 'A traffic flow failed'
    time.sleep(1)
emit({'event':'idle-start','at':now()})
time.sleep(35)
after = snapshot()
identity = lambda pods: {p['namespace']+'/'+p['name']:(p['uid'],p['restarts'],p['ready']) for p in pods}
emit({'event':'after','at':now(),'pods':after,'unchanged':identity(before)==identity(after)})
assert identity(before)==identity(after), 'Pod identity/readiness/restarts changed'
emit({'event':'completed','at':now()})
'''


def state():
    with urlopen('http://127.0.0.1:8765/api/state', timeout=5) as response:
        return json.load(response)


def validate(events, samples):
    rows = []
    stable_counts = {}
    for phase in [e for e in events if e['event'] == 'phase-end']:
        expected = {PORTS[f['user']+'/'+f['backend']][1]: f['direction'] for f in phase['flows']}
        stable = []
        for sample in samples:
            routes = [r for r in sample['data']['routes'] if r['namespace'] == 'cnf-multi-ue']
            if len(routes) == 4 and all(r.get('statsWindowStart') and
                    r['statsWindowStart'] >= phase['startedAt'] and r['statsAt'] <= phase['at'] for r in routes):
                stable.append(sample)
        assert stable, 'No complete observation window in phase ' + phase['phase']
        stable_counts[phase['phase']] = len(stable)
        for sample in stable:
            assert not sample['error']
            for route in [r for r in sample['data']['routes'] if r['namespace'] == 'cnf-multi-ue']:
                direction = expected.get(route['port'])
                if direction:
                    rate = route['txMbps' if direction == 'ul' else 'rxMbps']
                    assert rate is not None and .75 < rate < 1.25, (phase['phase'], route['port'], rate)
                else:
                    assert route['rxMbps'] == route['txMbps'] == 0, 'Inactive route has traffic'
            if phase['phase'] == 'C':
                ue1 = next(p for p in sample['data']['pods'] if p['component'] == 'UE1')
                assert all(i['rxMbps'] == i['txMbps'] == 0 for i in ue1['interfaceTraffic'])
                assert next(l for l in sample['data']['trafficLinks'] if l['source'] == ue1['id'])['status'] == 'idle'
        for flow in phase['flows']:
            result = flow['iperf']
            connected = result['start']['connected']
            assert all(c['local_host'] == next(e['ues'][flow['user']]['ip'] for e in events if e['event']=='ues') and
                       c['remote_port'] == PORTS[flow['user']+'/'+flow['backend']][0] for c in connected)
            assert result['start']['test_start']['reverse'] == (1 if flow['direction']=='dl' else 0)
            speed = result['end']['sum_received']['bits_per_second'] / 1e6
            assert .9 < speed < 1.1, (flow['user'], speed)
            rows.append({'phase':phase['phase'], 'user':flow['user'], 'backend':flow['backend'],
                         'direction':flow['direction'], 'receiverMbps':speed})
    assert len(rows) == 8
    assert events[-1]['event'] == 'completed'
    assert all(l['status']=='idle' for l in samples[-1]['data']['trafficLinks'])
    assert all(r['rxMbps']==r['txMbps']==0 for r in samples[-1]['data']['routes'])
    return {'passed':True, 'flows':rows, 'observationSamples':len(samples), 'stableWindowsPerPhase':stable_counts,
            'podsUnchanged':next(e['unchanged'] for e in events if e['event']=='after'),
            'idleAfterTraffic':True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    def save(name, value):
        (root/name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    baseline = state()
    save('before.json', baseline)
    assert baseline['source']=='ssh' and not baseline['error']
    assert all(c['status']=='pass' for c in baseline['data']['checks'])
    assert all(r['activeConnections']==0 for r in baseline['data']['routes']), 'Existing traffic detected'
    config = {'phases':PHASES, 'ports':PORTS}
    save('manifest.json', dict(config, route='R2', stage='R2.S4 platform observation support',
        configurationVersion='dashboard-mixed-traffic-v1', offeredMbpsPerFlow=1,
        secondsPerPhase=45, protocol='TCP', lock='~/.work/cnf-planner-live.lock',
        sourceSHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        evidenceDirectory=str(root), networkConfigurationMutations=False))
    remote = REMOTE.replace('__CONFIG__', repr(config))
    options = {'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform=='win32' else {}
    with (root/'stderr.txt').open('w', encoding='utf-8') as stderr:
        process = subprocess.Popen(['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=8',
            '-o','ServerAliveInterval=8','-o','ServerAliveCountMax=2','free5gc-vm-1','python3','-u','-'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
            text=True, encoding='utf-8', **options)
        process.stdin.write(remote)
        process.stdin.close()
        events, read_errors = [], []
        def read():
            try:
                with (root/'remote.jsonl').open('w', encoding='utf-8') as log:
                    for line in process.stdout:
                        log.write(line); log.flush()
                        event = json.loads(line)
                        events.append(event)
                        print(json.dumps({k:v for k,v in event.items() if k in ('event','phase','at','unchanged')}), flush=True)
            except Exception as exc:
                read_errors.append(str(exc))
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        samples, seen = [], None
        deadline = time.monotonic()+240
        try:
            while process.poll() is None:
                if time.monotonic()>deadline:
                    raise TimeoutError('Bounded experiment exceeded 240 seconds')
                sample = state()
                if sample['data']['observedAt'] != seen:
                    seen = sample['data']['observedAt']
                    samples.append(sample)
                    save('sample-%03d.json'%len(samples), sample)
                time.sleep(2)
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
            reader.join(timeout=5)
        assert process.returncode==0, (root/'stderr.txt').read_text(encoding='utf-8')
        assert not read_errors, read_errors
        final = state()
        save('after.json', final)
        samples.append(final)
        summary = validate(events, samples)
        save('summary.json', summary)
        print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
