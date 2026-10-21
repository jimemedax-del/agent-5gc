#!/usr/bin/env python3
"""Explicit E2 fault acceptance on the existing C1 fixture; never an Agent tool."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('live_fault_exercise', ROOT / 'scripts/cnf-planner-live.py')
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)
CASES = {'expired-plan': 'expired_plan', 'queue-drift': 'live_state_drift',
         'partial-write': 'tc_apply_failed', 'measurement-interrupted': 'acquisition_failed'}
BASE_BACKEND = live.Backend


class FaultBackend(BASE_BACKEND):
    def __init__(self, case, folder):
        super().__init__()
        self.case, self.folder = case, folder
        self.writes, self.fixture_attempted, self.fixture_cleaned = [], False, False

    def write_batch(self, lines, folder):
        actual = list(lines)
        if self.case == 'partial-write' and folder.name == 'execution':
            live.need(actual == live.c.tc_commands('equal'), 'unexpected_write', 'Fault only replaces the fixed equal batch')
            actual = [actual[0], actual[0]]  # First add succeeds; duplicate add genuinely fails in TC.
        record = {'folder': folder.relative_to(self.folder).as_posix(), 'commands': actual,
                  'startedAt': live.now().isoformat()}
        try:
            return super().write_batch(actual, folder)
        except Exception as error:
            record.update(error=str(error), errorCode=getattr(error, 'code', type(error).__name__))
            raise
        finally:
            try:
                record['tcAfter'] = self.tc()
            finally:
                record['endedAt'] = live.now().isoformat()
                self.writes.append(record)
                live.save(self.folder / 'writes.json', self.writes)

    def install_fixture_drift(self):
        folder = self.folder / 'fixture-install'
        folder.mkdir()
        live.check_queue(self.tc(), 'none')
        self.fixture_attempted = True
        self.write_batch(live.c.tc_commands('equal'), folder)
        live.check_queue(self.tc(), 'equal')
        live.save(self.folder / 'fault.json', {'kind': self.case, 'createdAt': live.now().isoformat(),
                  'owner': 'exercise_fixture_actor', 'adapterMutationAttempted': self.mutation_attempted})

    def clean_fixture_drift(self):
        if not self.fixture_attempted or self.fixture_cleaned:
            return
        folder = self.folder / 'fixture-teardown'
        folder.mkdir()
        self.check_pod()
        tc = self.tc()
        live.save(folder / 'tc-before.json', tc)
        try:
            live.check_queue(tc, 'none')
        except ValueError:
            live.owned_partial(tc)
            self.write_batch(['qdisc del dev eth0 root handle 1:'], folder)
        live.check_queue(self.tc(), 'none')
        self.fixture_cleaned = True

    def full(self, folder):
        # Fixture actor removes its own injected state only after the adapter has refused.
        if folder.name == 'final':
            self.clean_fixture_drift()
        return super().full(folder)

    def __exit__(self, *args):
        try:
            self.clean_fixture_drift()
        finally:
            super().__exit__(*args)


def exercise_case(case, folder):
    live.need(case in CASES, 'unknown_case', case)
    folder.mkdir()
    live.save(folder / 'writes.json', [])
    backend = FaultBackend(case, folder)
    actual_apply, actual_kube = live.apply_plan, live.c.m1.kube
    injected, samples = False, 0

    def apply(plan, batch, state, active, out):
        nonlocal injected
        if case == 'expired-plan':
            started = live.now()
            delay = max(0, (live.p.instant(plan['validUntil']) - started).total_seconds()) + 1
            live.need(delay <= 32, 'unexpected_expiry_wait', 'Only the 30 s E2 plan horizon may be exercised')
            time.sleep(delay)  # Real wall time; no live clock override.
            live.save(folder / 'fault.json', {'kind': case, 'waitStartedAt': started.isoformat(),
                      'waitEndedAt': live.now().isoformat(), 'waitSeconds': delay, 'planValidUntil': plan['validUntil']})
            injected = True
        elif case == 'queue-drift':
            active.install_fixture_drift()
            injected = True
        return actual_apply(plan, batch, state, active, out)

    def kube(*args, **kwargs):
        nonlocal injected, samples
        value = actual_kube(*args, **kwargs)
        command_path = folder / 'run/measurement/ue1/iperf-command.json'
        if case == 'measurement-interrupted' and not injected and '/sys/fs/cgroup/memory.current' in args and command_path.exists():
            samples += 1
            if samples >= 2:
                command = live.read(command_path)
                state = live.read(folder / 'run/live-state.json')
                token = state['liveStateHash'][:24]
                pod = backend.topology['users']['ue1']['pod']
                pattern = live.re.escape(' '.join(command[6:]))
                match = actual_kube('-n', 'free5gc', 'exec', pod, '--', 'pgrep', '-f', '-x', '--', pattern)
                pids = match.stdout.strip().splitlines()
                live.need(len(pids) == 1 and pids[0].isdigit(), 'fault_target_ambiguous', 'Require one exact owned measurement process')
                cmdline = actual_kube('-n', 'free5gc', 'exec', pod, '--', 'cat', '/proc/' + pids[0] + '/cmdline').stdout
                live.need(cmdline.rstrip('\0').split('\0') == command[6:], 'fault_target_drift', 'Live PID command differs')
                injected = True
                stopped = live.stop_owned_process(pod, command, token)
                live.save(folder / 'fault.json', {'kind': case, 'createdAt': live.now().isoformat(), 'pod': pod,
                          'pid': pids[0], 'command': command, 'rawCmdline': cmdline, 'token': token,
                          'stop': {'returncode': stopped.returncode, 'stdout': stopped.stdout, 'stderr': stopped.stderr}})
                live.need(stopped.returncode == 0, 'fault_not_delivered', 'Owned receiver interruption failed')
        return value

    caught = None
    try:
        with patch.object(live, 'Backend', return_value=backend), patch.object(live, 'apply_plan', side_effect=apply), \
             patch.object(live.c.m1, 'kube', side_effect=kube):
            live.run(folder / 'run')
    except Exception as error:
        caught = error
    summary = live.read(folder / 'run/summary.json')
    expected = CASES[case]
    live.need(caught is not None and getattr(caught, 'code', None) == expected and summary.get('errorCode') == expected,
              'unexpected_fault_outcome', case + ': ' + str(caught))
    live.need(summary['outcome'] == 'failed' and summary['finalStrictBaselinePassed'] and
              summary['original26PodIdentitiesUnchanged'], 'fault_final_check_failed', case)
    live.need(summary['restoration'] == ('restored' if case in ['partial-write', 'measurement-interrupted'] else 'not_required_yet'),
              'fault_restore_failed', case)
    live.need(injected or case == 'partial-write', 'fault_not_injected', case)
    acceptance = {'case': case, 'outcome': 'passed_expected_failure', 'expectedErrorCode': expected,
                  'actualRunOutcome': summary['outcome'], 'adapterMutationAttempted': backend.mutation_attempted,
                  'adapterApplyCompleted': backend.apply_completed, 'fixtureActorMutationAttempted': backend.fixture_attempted,
                  'fixtureActorCleaned': backend.fixture_cleaned, 'finalStrictBaselinePassed': True}
    live.save(folder / 'acceptance.json', acceptance)
    print(json.dumps(acceptance), flush=True)
    return acceptance


def exercise(folder):
    live.need(sys.platform == 'linux', 'unsupported_platform', 'Run only on existing VM1')
    folder.mkdir(parents=True, exist_ok=False)
    live.archive_inputs(folder)
    summary = {'route': 'R2', 'stage': 'R2.S4', 'task': 'E2-fault-acceptance', 'outcome': 'failed',
               'startedAt': live.now().isoformat(), 'cases': [], 'scope': 'controlled_C1_equal_faults_and_normal_regression'}
    try:
        for case in CASES:
            print(json.dumps({'startingCase': case}), flush=True)
            summary['cases'].append(exercise_case(case, folder / case))
            live.save(folder / 'summary.json', summary)
        print(json.dumps({'startingCase': 'normal-control'}), flush=True)
        live.run(folder / 'normal-control')
        summary['outcome'] = 'passed_fault_acceptance'
    except Exception as error:
        summary.update(error=str(error), errorCode=getattr(error, 'code', type(error).__name__))
        raise
    finally:
        summary['endedAt'] = live.now().isoformat()
        live.save(folder / 'summary.json', summary)
        print(json.dumps(summary), flush=True)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    exercise(args.out)
