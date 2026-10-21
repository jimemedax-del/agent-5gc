#!/usr/bin/env python3
"""E2: observed-state, plan-bound C1 equal-mode experiment with owned restoration."""
import argparse
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / 'infra/cnfs/planner-live-contract.json'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


p = load('e1_planner', ROOT / 'scripts/cnf-planner.py')
c = load('e2_c1', ROOT / 'scripts/cnf-contention.py')
c2 = load('e2_c2', ROOT / 'scripts/cnf-contention-uplink.py')
auditor = load('e2_raw_auditor', ROOT / 'scripts/audit-cnf-contention.py')
need, read, digest = p.need, p.read_json, p.digest


def save(path, value):
    path.write_text(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def now():
    return datetime.now(timezone.utc)


def contract():
    value = read(CONTRACT)
    need(value['contractVersion'] == 'cnf-planner-live-v1' and value['allowedInitialMode'] == 'none' and
         value['allowedTargetMode'] == 'equal' and value['stateTtlSeconds'] == 180 and value['planStartTtlSeconds'] == 30 and
         value['requestTargets'] == {'direction': 'downlink', 'min_bandwidth_mbps': 4, 'max_bandwidth_mbps': 8,
                                    'priority': 'normal', 'valid_for_seconds': 30, 'measurement_window_seconds': 20},
         'unsupported_live_contract', 'Only the frozen E2 equal-mode experiment is supported')
    return value


def adapter_hash():
    sources = {f.relative_to(ROOT).as_posix(): p.file_hash(f) for directory in ['scripts', 'infra']
               for f in sorted((ROOT / directory).rglob('*')) if f.is_file() and '__pycache__' not in f.parts}
    return digest({'sources': sources, 'catalogue': p.catalogue()['catalogHash']})


def result(tool, status, **values):
    return {'toolVersion': 'cnf-planner-live-v1', 'tool': tool, 'status': status, **values}


def mode_of(tc):
    for mode in ['none', 'equal']:
        try:
            check_queue(tc, mode)
            return mode
        except (ValueError, KeyError, TypeError):
            continue
    raise p.Rejection('unknown_queue', 'E2 refuses nonempty/foreign/unsupported queue configurations')


def check_queue(tc, mode):
    auditor.check_tc(tc, mode)
    if mode == 'none':
        need(len(tc['qdisc']) == 1 and tc['qdisc'][0]['handle'] == '0:', 'foreign_queue', 'Unexpected extra initial queue')
    else:
        owned_partial(tc)
        need(len(tc['qdisc']) == 4 and len(tc['class']) == 4 and len(tc['filter']) == 6,
             'foreign_queue', 'Unexpected extra queue components')


def session_record(supi, user, sessions):
    need(sessions.count('PDU Session') == 1 and 'state: PS-ACTIVE' in sessions and
         'address: ' + user['ip'] in sessions and 'ambr: up[1000Mb/s] down[1000Mb/s]' in sessions,
         'session_drift', 'PDU session, address or AMBR differs')
    normalized = '\n'.join(line.strip() for line in sessions.splitlines() if 'data-pending:' not in line)
    proof = {'supi': supi, 'pod': user['pod'], 'podUID': user['podUID'], 'ip': user['ip'], 'sessionListing': normalized}
    return {**proof, 'session_id': 'pdu-' + digest(proof)[:24]}


def live_state(observation, observed_at=None):
    stamp = observed_at or now()
    return p.seal({'liveStateVersion': 'cnf-live-state-v1', 'observedAt': stamp.isoformat(),
                   'validUntil': (stamp + timedelta(seconds=contract()['stateTtlSeconds'])).isoformat(),
                   'adapterHash': adapter_hash(), 'fingerprint': digest(observation),
                   'observation': observation, 'liveStateObserved': True,
                   'bindingScope': 'observed_fixture_SUPI_Pod_container_PDU_listing_TUN_IP'}, 'liveState')


def check_state(state, at=None):
    body = p.check_seal(state, 'liveState')
    p.keys(body, ['liveStateVersion', 'observedAt', 'validUntil', 'adapterHash', 'fingerprint', 'observation',
                  'liveStateObserved', 'bindingScope'], 'live state')
    need(state['liveStateVersion'] == 'cnf-live-state-v1' and state['adapterHash'] == adapter_hash(),
         'adapter_drift', 'Live adapter/catalogue version changed')
    need(digest(state['observation']) == state['fingerprint'], 'observation_hash_mismatch', 'Observed state changed')
    stamp = at or now()
    need(state['liveStateObserved'] is True and state['bindingScope'] == 'observed_fixture_SUPI_Pod_container_PDU_listing_TUN_IP' and
         p.instant(state['validUntil']) == p.instant(state['observedAt']) + timedelta(seconds=contract()['stateTtlSeconds']),
         'invalid_live_state', 'Observed flag, binding scope or freshness horizon differs')
    need(p.instant(state['observedAt']) <= stamp < p.instant(state['validUntil']), 'stale_live_state', 'Observe again; do not refresh timestamps')
    need(state['observation']['queueMode'] == 'none', 'unsupported_initial_queue', 'Start from the observed unshaped fixture')


def requests_for(state):
    """Explicit experiment inputs, never defaults for the user's six natural-language requests."""
    return {'requestVersion': 'cnf-planner-requests-v1', 'batch_id': 'e2-equal-' + state['liveStateId'],
            'assessmentScope': 'controlled_receiver_steady_mean', 'protocol': 'UDP', 'requests': [
                {'request_id': 'equal-' + slot, 'user_id': binding['supi'], 'session_id': binding['session_id'],
                 'requirements': copy.deepcopy(contract()['requestTargets'])}
                for slot, binding in sorted(state['observation']['bindings'].items())]}


def computation_state(state):
    source = {'sourceVersion': 'cnf-planner-state-input-v1', 'mode': 'declared_fixture', 'direction': 'downlink',
              'declaredAt': state['observedAt'], 'ttlSeconds': contract()['stateTtlSeconds'], 'capacityMbps': 10,
              'unmanagedReservedMbps': 0, 'unmanagedTrafficDetected': False, 'nativeQosEnabled': False,
              'activeProfile': 'none', 'bindings': [{'slot': slot, 'user_id': b['supi'], 'session_id': b['session_id']}
                                                   for slot, b in sorted(state['observation']['bindings'].items())]}
    # E1 is explicitly a calculation projection; the outer live fingerprint supplies the observation binding.
    return p._state(source, p.catalogue(), now())


def plan_policy(state, batch):
    check_state(state)
    need(batch == requests_for(state), 'unsupported_live_request', 'E2 only accepts explicitly configured equal fixture targets')
    computational = computation_state(state)
    inner = p.plan_policy(batch, computational)
    need(inner['status'] == 'planned' and inner['plan']['mode'] == 'equal', 'no_equal_plan', 'E1 did not produce the allowed mode')
    plan = p.seal({'livePlanVersion': 'cnf-live-plan-v1', 'liveStateHash': state['liveStateHash'],
                   'fingerprint': state['fingerprint'], 'adapterHash': adapter_hash(), 'batchHash': digest(batch),
                   'innerPlan': inner['plan'], 'computationState': computational,
                   'validUntil': inner['plan']['validUntil']}, 'livePlan')
    return result('plan_policy', 'planned', plan=plan, executionAuthorized=False)


def validate_plan(plan, batch, state, observation):
    body = p.check_seal(plan, 'livePlan')
    p.keys(body, ['livePlanVersion', 'liveStateHash', 'fingerprint', 'adapterHash', 'batchHash',
                  'innerPlan', 'computationState', 'validUntil'], 'live plan')
    check_state(state)
    need(plan['livePlanVersion'] == 'cnf-live-plan-v1' and plan['adapterHash'] == adapter_hash() and
         plan['liveStateHash'] == state['liveStateHash'] and plan['batchHash'] == digest(batch) and
         plan['fingerprint'] == state['fingerprint'] and digest(observation) == state['fingerprint'],
         'live_state_drift', 'Plan, requests, sessions, resources or queues differ from the observed snapshot')
    need(batch == requests_for(state), 'unsupported_live_request', 'Live request targets changed')
    need(plan['computationState'] == computation_state(state), 'projection_drift', 'Offline projection is not derived from observed bindings')
    validation = p.validate_plan(plan['innerPlan'], batch, plan['computationState'])
    need(validation['status'] == 'valid_offline' and plan['innerPlan']['mode'] == 'equal' and
         plan['validUntil'] == plan['innerPlan']['validUntil'], 'invalid_inner_plan', 'E1 validation or live scope failed')
    return result('validate_plan', 'valid_live_preconditions', livePlanHash=plan['livePlanHash'],
                  checkedAt=now().isoformat(), executionAuthorized=True, authorizationScope='current_locked_run_equal_mode_only')


def owned_partial(tc):
    """Recognize only a prefix of our known additive TC batch after an ordinary apply error."""
    if 'raw' in tc:
        parsed = auditor.parse_tc_native(json.loads(tc['raw']['qdisc']), tc['raw']['class'], tc['raw']['filter'])
        need(all(tc[k] == v for k, v in parsed.items()), 'foreign_partial_queue', 'Native partial TC output differs')
    roots = [q for q in tc['qdisc'] if q.get('root')]
    need(len(roots) == 1 and roots[0]['kind'] == 'htb' and roots[0]['handle'] == '1:' and
         roots[0]['options']['default'] == '0x99' and roots[0]['options']['r2q'] == 10,
         'foreign_partial_queue', 'Partial root is not the one just created by this run')
    expected = {'1:1': (1250000, 1250000, 0), '1:10': (500000, 1000000, 0),
                '1:20': (500000, 1000000, 0), '1:99': (12500, 1250000, 7)}
    need(len({x['handle'] for x in tc['class']}) == len(tc['class']) and
         len({x['handle'] for x in tc['qdisc']}) == len(tc['qdisc']), 'foreign_partial_queue', 'Duplicate partial handles')
    for cls in tc['class']:
        need(cls['kind'] == 'htb' and cls['handle'] in expected and
             tuple(cls['options'][k] for k in ['rate', 'ceil', 'prio']) == expected[cls['handle']] and
             cls['parent'] == ('1:' if cls['handle'] == '1:1' else '1:1'), 'foreign_partial_queue', 'Foreign class in partial queue')
        if 'raw' in tc:
            blocks = [block for block in re.split(r'(?=^class )', tc['raw']['class'], flags=re.M)
                      if block.startswith('class htb ' + cls['handle'] + ' ')]
            need(len(blocks) == 1 and 'burst 32Kb/' in blocks[0] and 'cburst 32Kb/' in blocks[0] and
                 (cls['handle'] == '1:1' or 'quantum 1514 ' in blocks[0]),
                 'foreign_partial_queue', 'Partial class burst/quantum changed')
    for q in tc['qdisc']:
        if not q.get('root'):
            need(q['kind'] == 'fq_codel' and q.get('parent') in ['1:10', '1:20', '1:99'] and
                 q['handle'] == q['parent'].split(':')[1] + ':' and
                 q['options']['limit'] == 256 and 4990 <= q['options']['target'] <= 5010 and
                 99990 <= q['options']['interval'] <= 100010 and q['options']['quantum'] == 1464 and
                 not q['options'].get('ecn', False), 'foreign_partial_queue', 'Foreign leaf in partial queue')
    expected_matches = set()
    for handle, port, tos in [('1:10', 5201, 40), ('1:20', 5202, 80)]:
        for proto in [6, 17]:
            expected_matches.add((handle, 10, ((f'{proto << 16:08x}', '00ff0000', 8), (f'{port << 16:08x}', 'ffff0000', 20))))
        expected_matches.add((handle, 5, (('00010000', '00ff0000', 8), (f'{tos << 16:08x}', '00fc0000', 0))))
    actual = {(f['options']['flowid'], f['pref'], tuple(tuple(m) for m in f['options']['matches'])) for f in tc['filter']}
    need(actual <= expected_matches and len(actual) == len(tc['filter']) and
         all(f['kind'] == 'u32' and f['parent'] == '1:' and f['protocol'] == 'ip' for f in tc['filter']),
         'foreign_partial_queue', 'Foreign classifier in partial queue')
    return True


class Backend:
    def __init__(self):
        self.topology, self.pod = None, None
        self.locked, self.mutation_attempted, self.apply_completed = False, False, False
        self.baseline = None

    def __enter__(self):
        import fcntl
        path = Path.home() / '.work/cnf-planner-live.lock'
        path.parent.mkdir(exist_ok=True)
        self.fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            raise p.Rejection('live_adapter_busy', 'Another E2 run owns the adapter lock') from None
        self.locked = True
        return self

    def __exit__(self, *args):
        self.locked = False
        os.close(self.fd)

    def check_pod(self):
        actual = json.loads(c.m1.kube('-n', c.NS, 'get', 'pod', self.pod, '-o', 'json').stdout)
        need(actual['metadata']['uid'] == self.topology['contention']['uid'] and not actual['metadata'].get('deletionTimestamp'),
             'endpoint_replaced', 'Refuse to act on a replacement or terminating endpoint')

    def queue(self, *args):
        return c.m1.kube('-n', c.NS, 'exec', self.pod, '-c', 'queue', '--', *args).stdout

    def tc(self):
        raw = {'qdisc': self.queue('tc', '-j', '-s', 'qdisc', 'show', 'dev', 'eth0'),
               'class': self.queue('tc', '-s', '-d', 'class', 'show', 'dev', 'eth0'),
               'filter': self.queue('tc', '-s', 'filter', 'show', 'dev', 'eth0')}
        return {**auditor.parse_tc_native(json.loads(raw['qdisc']), raw['class'], raw['filter']), 'raw': raw}

    def resources(self):
        records = []
        for ns in ['free5gc', 'cnf-lab', 'cnf-multi-ue', c.NS, c2.NS]:
            data = json.loads(c.m1.kube('-n', ns, 'get', 'deployments,statefulsets,services,configmaps,networkpolicies', '-o', 'json').stdout)
            for obj in data['items']:
                # Hash configuration rather than copying possibly sensitive ConfigMap bodies into summaries.
                records.append({'namespace': ns, 'kind': obj['kind'], 'name': obj['metadata']['name'],
                                'uid': obj['metadata']['uid'], 'bodyHash': digest(obj.get('spec', obj.get('data', {})))})
        return sorted(records, key=lambda r: (r['namespace'], r['kind'], r['name']))

    def full(self, folder):
        folder.mkdir()
        _, self.topology = c.verify(folder / 'c1')
        self.pod = self.topology['contention']['pod']
        self.baseline = self.observe(folder / 'observed')
        need(self.baseline['queueMode'] == 'none', 'unsupported_initial_queue', 'E2 must start unshaped')
        return self.baseline

    def observe(self, folder):
        folder.mkdir()
        self.check_pod()
        pods = c2.snapshot()
        c2.audit.identities(pods)
        uplink = c2.network()
        c2.audit.check_network(uplink, clean=True)
        tc = self.tc()
        mode = mode_of(tc)
        qos = c.native_switch()
        need(qos.strip() == 'QoS Enable: 0', 'native_qos_unaccepted', 'Native UPF policer must remain disabled')
        bindings = {}
        for slot, user in sorted(self.topology['users'].items()):
            actual_pod = pods['free5gc'][user['pod']]
            need(actual_pod['uid'] == user['podUID'], 'ue_replaced', slot)
            ids = c.m1.kube('-n', 'free5gc', 'exec', user['pod'], '--', './nr-cli', '-d').stdout.strip().splitlines()
            status = c.m1.kube('-n', 'free5gc', 'exec', user['pod'], '--', './nr-cli', user['supi'], '-e', 'status').stdout
            sessions = c.m1.kube('-n', 'free5gc', 'exec', user['pod'], '--', './nr-cli', user['supi'], '-e', 'ps-list').stdout
            address = json.loads(c.m1.kube('-n', 'free5gc', 'exec', user['pod'], '--', 'ip', '-j', '-4', 'addr', 'show', 'uesimtun0').stdout)
            ips = [a['local'] for dev in address for a in dev['addr_info'] if a['family'] == 'inet']
            need(ids == [user['supi']] and 'rm-state: RM-REGISTERED' in status and ips == [user['ip']], 'ue_binding_drift', slot)
            for process in ['iperf3', 'ping']:
                need(c.m1.kube('-n', 'free5gc', 'exec', user['pod'], '--', 'pgrep', '-x', process, required=False).returncode == 1,
                     'unmanaged_test_process', slot + ': ' + process)
            bindings[slot] = session_record(user['supi'], user, sessions)
            save(folder / (slot + '-sessions.txt'), sessions)
        observation = {'pods': pods, 'resources': self.resources(), 'bindings': bindings, 'queueMode': mode,
                       'queueShape': auditor.tc_shape(tc), 'uplinkClean': True, 'nativeQos': qos.strip(),
                       'endpoint': {'pod': self.pod, 'uid': self.topology['contention']['uid'], 'ip': self.topology['contention']['ip']}}
        save(folder / 'tc.json', tc)
        save(folder / 'c2-network.json', uplink)
        save(folder / 'observation.json', observation)
        return observation

    def write_batch(self, lines, folder):
        need(self.locked, 'missing_lock', 'Mutation requires the held E2 process lock')
        self.check_pod()
        save(folder / 'tc-batch.txt', '\n'.join(lines) + '\n')
        process = subprocess.run(['kubectl', '-n', c.NS, 'exec', '-i', self.pod, '-c', 'queue', '--', 'tc', '-batch', '-'],
                                 input='\n'.join(lines) + '\n', text=True, capture_output=True, timeout=30)
        save(folder / 'tc-command.json', {'returncode': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr})
        need(process.returncode == 0, 'tc_apply_failed', 'See saved TC command result')
        self.check_pod()


def inspect_state(backend, folder):
    observation = backend.full(folder)
    state = live_state(observation)
    return result('inspect_state', 'observed', state=state, executionAuthorized=False)


def apply_plan(plan, batch, state, backend, folder):
    need(backend.locked, 'missing_lock', 'Hold the adapter lock across apply/measure/restore')
    folder.mkdir()
    observation = backend.observe(folder / 'recheck')
    validation = validate_plan(plan, batch, state, observation)
    save(folder / 'validation.json', validation)
    before = backend.tc()
    check_queue(before, 'none')
    save(folder / 'tc-before.json', before)
    save(folder / 'write-intent.json', {'livePlanHash': plan['livePlanHash'], 'endpoint': observation['endpoint'],
                                       'initialQueueMode': 'none', 'allowedTargetMode': 'equal', 'ordinaryFailureCleanupOnly': True,
                                       'startedAt': now().isoformat()})
    need(now() <= p.instant(plan['validUntil']), 'expired_plan', 'Plan expired before TC write')
    backend.mutation_attempted = True
    backend.write_batch(c.tc_commands('equal'), folder)
    after = backend.tc()
    save(folder / 'tc-after.json', after)
    check_queue(after, 'equal')
    backend.apply_completed = True
    return result('apply_plan', 'applied', livePlanHash=plan['livePlanHash'], completedAt=now().isoformat(),
                  networkChanged=True, queueShapeAfter=auditor.tc_shape(after))


def capture(backend, state, folder):
    """Use the original C1 raw format, without its implicit second queue application."""
    folder.mkdir()
    tc = backend.tc()
    check_queue(tc, 'equal')
    save(folder / 'tc-after.json', tc)
    c.m1.assert_steady(state['observation']['pods'], c2.snapshot())
    upf, _ = c.m1.one_pod(state['observation']['pods'], 'free5gc', 'free5gc-helm-free5gc-upf-upf')
    initial = c.m1.upf_observation(folder, 'before', upf)
    save(folder / 'qos-before.txt', c.native_switch())
    jobs, samples = [], []
    token = state['liveStateHash'][:24]
    summary = {'outcome': 'failed', 'mode': 'equal', 'round': 1, 'startedAt': c.m1.now()}
    try:
        for user in c.config()['users']:
            sub = folder / user['name']
            sub.mkdir()
            live = backend.topology['users'][user['name']]
            args = ['kubectl', '-n', 'free5gc', 'exec', live['pod'], '--', 'iperf3', '-c', backend.topology['target'],
                    '-B', live['ip'], '-p', str(user['nodePort']), '-u', '-R', '-P', '1', '-b', '8M', '-l', '1200',
                    '-t', '30', '--tos', str(user['pingTos']), '-J', '--get-server-output', '--connect-timeout', '5000',
                    '--extra-data', token]
            ping = c.ping_args(user, backend.topology, 150)
            ping = ping[:-1] + ['-p', token] + ping[-1:]
            for kind, command in [('iperf', args), ('ping', ping)]:
                save(sub / (kind + '-command.json'), command)
                stdout = (sub / (kind + ('.json' if kind == 'iperf' else '.txt'))).open('w', encoding='utf-8')
                stderr = (sub / (kind + '-stderr.txt')).open('w', encoding='utf-8')
                job = subprocess.Popen(command, stdout=stdout, stderr=stderr, text=True)
                jobs.append((live, command, job, stdout, stderr))
        deadline = time.monotonic() + 60
        while any(job.poll() is None for _, _, job, _, _ in jobs):
            current = c2.snapshot()
            save(folder / 'guard-pods.json', current)
            c.m1.assert_steady(state['observation']['pods'], current)
            memory = int(c.m1.kube('-n', 'free5gc', 'exec', upf, '--', 'cat', '/sys/fs/cgroup/memory.current', timeout=5).stdout)
            samples.append({'time': c.m1.now(), 'upfMemoryBytes': memory})
            save(folder / 'resource-samples.json', samples)
            need(memory < 536870912 and time.monotonic() < deadline, 'runtime_guard', 'Memory or acquisition deadline exceeded')
            time.sleep(.5)
        for _, _, job, stdout, stderr in jobs:
            stdout.close()
            stderr.close()
            need(job.returncode == 0, 'acquisition_failed', 'Receiver or RTT process failed')
        summary.update(outcome='captured', endedAt=c.m1.now())
        save(folder / 'tc-end.json', backend.tc())
        save(folder / 'qos-after.txt', c.native_switch())
        final = c.m1.upf_observation(folder, 'after', upf)
        need(all(y > x for x, y in zip(c.m1.path_counters(initial['interfaces']), c.m1.path_counters(final['interfaces']))),
             'missing_upf_path', 'UPF path counters did not increase')
        for user in c.config()['users']:
            save(folder / (user['name'] + '-server.txt'), c.m1.kube('-n', c.NS, 'logs', backend.pod,
                 '-c', user['name'], '--since-time=' + summary['startedAt']).stdout)
        c.m1.assert_steady(state['observation']['pods'], c2.snapshot())
    except Exception as error:
        summary.update(outcome='failed', error=str(error), endedAt=c.m1.now())
        raise
    finally:
        for live, command, job, stdout, stderr in jobs:
            if job.poll() is None:
                job.terminate()
                stop_owned_process(live['pod'], command, token)
                job.wait(timeout=10)
            stdout.close()
            stderr.close()
        save(folder / 'summary.json', summary)
    return summary


def stop_owned_process(pod, command, token):
    """GNU pkill exact full-argv match, including this acquisition's unique marker."""
    remote = command[6:]
    need(command[:6] == ['kubectl', '-n', 'free5gc', 'exec', pod, '--'] and
         remote[0] in ['iperf3', 'ping'] and re.fullmatch('[0-9a-f]{24}', token) and token in remote and
         (('--extra-data' in remote and remote[remote.index('--extra-data') + 1] == token) if remote[0] == 'iperf3'
          else ('-p' in remote and remote[remote.index('-p') + 1] == token)),
         'not_owned_process', 'Refuse process cleanup without the acquisition marker')
    pattern = re.escape(' '.join(remote))
    return c.m1.kube('-n', 'free5gc', 'exec', pod, '--', 'pkill', '-TERM', '-f', '-x', '--', pattern, required=False)


def verify_plan(plan, batch, topology, folder):
    observation = auditor.group(folder, topology, c.config())
    performance = auditor.performance([{'folder': 'measurement', 'mode': 'equal', **observation}], c.config())
    targets = {r['user_id']: r['requirements'] for r in batch['requests']}
    comparisons = {}
    for slot, user in topology['users'].items():
        demand = targets[user['supi']]
        rate = observation['users'][slot]['steadyMbps']
        comparisons[slot] = {'receivedSteadyMbps': rate, 'minimumMbps': demand['min_bandwidth_mbps'],
                             'maximumMbps': demand['max_bandwidth_mbps'],
                             'passed': demand['min_bandwidth_mbps'] <= rate <= demand['max_bandwidth_mbps']}
    passed = all(x['passed'] for x in comparisons.values()) and performance['passed']
    return result('verify_plan', 'passed_controlled_targets' if passed else 'failed_targets', livePlanHash=plan['livePlanHash'],
                  evidenceIntegrity='passed', targetComparisons=comparisons, observation=observation,
                  historicalFixtureChecks=performance, latencyLossGuaranteesAccepted=False)


def restore(state, backend, folder):
    need(backend.locked, 'missing_lock', 'Restore requires the held owner lock')
    folder.mkdir()
    backend.check_pod()
    before = backend.tc()
    save(folder / 'tc-before.json', before)
    try:
        check_queue(before, 'none')
    except ValueError:
        need(backend.mutation_attempted, 'not_owned', 'No mutation intent from this process')
        if backend.apply_completed:
            check_queue(before, 'equal')
        else:
            owned_partial(before)
        backend.write_batch(['qdisc del dev eth0 root handle 1:'], folder)
    after = backend.tc()
    save(folder / 'tc-after.json', after)
    check_queue(after, 'none')
    observed = backend.observe(folder / 'recheck')
    need(digest(observed) == state['fingerprint'], 'restore_state_drift', 'Queue cleared but original identities/resources/sessions differ')
    return result('restore', 'restored', queueRestored=True, originalObservedStateMatched=True)


def archive_inputs(folder):
    hashes = {}
    for directory in ['scripts', 'infra', 'tests']:
        for source in sorted((ROOT / directory).rglob('*')):
            if not source.is_file() or '__pycache__' in source.parts:
                continue
            relative = source.relative_to(ROOT)
            target = folder / 'inputs' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            hashes[relative.as_posix()] = p.file_hash(source)
    for relative in ['results/20261020-cnf-contention/metadata.json', 'results/20261020-cnf-contention-uplink/metadata.json']:
        target = folder / 'inputs' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
        hashes[relative] = p.file_hash(ROOT / relative)
    save(folder / 'input-hashes.json', hashes)
    return hashes


def run(folder):
    need(sys.platform == 'linux', 'unsupported_platform', 'Run on the existing VM1 with its KUBECONFIG')
    folder.mkdir(parents=True, exist_ok=False)
    contract()
    archive_inputs(folder)
    overall = {'outcome': 'failed', 'route': 'R2', 'stage': 'R2.S4', 'task': 'E2', 'startedAt': now().isoformat(),
               'scope': 'single_existing_C1_equal_downlink_run', 'restoration': 'not_required_yet'}
    try:
        with Backend() as backend:
            state = inspect_state(backend, folder / 'initial')['state']
            save(folder / 'live-state.json', state)
            batch = requests_for(state)
            save(folder / 'requests.json', batch)
            plan = plan_policy(state, batch)['plan']
            save(folder / 'plan.json', plan)
            try:
                execution = apply_plan(plan, batch, state, backend, folder / 'execution')
                save(folder / 'execution.json', execution)
                capture(backend, state, folder / 'measurement')
                verified = verify_plan(plan, batch, backend.topology, folder / 'measurement')
                save(folder / 'verification.json', verified)
                need(verified['status'] == 'passed_controlled_targets', 'target_failure', 'Measured targets failed')
            except Exception as error:
                save(folder / 'execution-error.json', {'error': str(error), 'mutationAttempted': backend.mutation_attempted,
                                                       'applyCompleted': backend.apply_completed})
                raise
            finally:
                if backend.mutation_attempted:
                    try:
                        restoration = restore(state, backend, folder / 'restore')
                        save(folder / 'restoration.json', restoration)
                        overall['restoration'] = restoration['status']
                    except Exception as error:
                        overall['restoration'] = 'failed: ' + str(error)
                        save(folder / 'restoration.json', result('restore', 'failed', error=str(error)))
                        raise
            final = backend.full(folder / 'final')
            need(digest(final) == state['fingerprint'], 'final_state_drift', 'Final strict baseline does not match initial observed state')
            overall.update(outcome='passed_controlled_scope', livePlanId=plan['livePlanId'],
                           original26PodIdentitiesUnchanged=True, finalStrictBaselinePassed=True)
    except Exception as error:
        overall['error'] = str(error)
        raise
    finally:
        overall['endedAt'] = now().isoformat()
        save(folder / 'summary.json', overall)
        print(json.dumps(overall, ensure_ascii=False), flush=True)
    return overall


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', 'inspect'])
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'run':
        run(args.out)
    else:
        args.out.mkdir(parents=True, exist_ok=False)
        archive_inputs(args.out)
        with Backend() as backend:
            observed = inspect_state(backend, args.out / 'live')
            save(args.out / 'live-state.json', observed['state'])
            print(json.dumps({'status': observed['status'], 'liveStateId': observed['state']['liveStateId']}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('E2 ERROR: ' + str(error), file=sys.stderr)
        sys.exit(1)
