#!/usr/bin/env python3
"""E3: complete requests select one observed C1 downlink policy; locked execution."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('e3_live_helpers', ROOT / 'scripts/cnf-planner-live.py')
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)
p, need, read, save, digest = live.p, live.need, live.read, live.save, live.digest
CONTRACT = ROOT / 'infra/cnfs/planner-policy-contract.json'
MODES = ('equal', 'guarantee-ue1', 'guarantee-ue2')


def contract():
    value = read(CONTRACT)
    need(value['contractVersion'] == 'cnf-planner-policy-v1' and value['allowedInitialMode'] == 'none' and
         value['allowedTargetModes'] == list(MODES) and value['stateTtlSeconds'] == 180 and value['planStartTtlSeconds'] == 30,
         'unsupported_policy_contract', 'Only the E3 C1 downlink whitelist is supported')
    return value


def result(tool, status, **values):
    return {'toolVersion': 'cnf-planner-policy-v1', 'tool': tool, 'status': status, **values}


def unwrap(value, field):
    return value[field] if isinstance(value, dict) and value.get('toolVersion') in ['cnf-planner-policy-v1', 'cnf-planner-live-v1'] and field in value else value


class Backend(live.Backend):
    queue_modes = ('none', *MODES)


def inspect_state(backend, folder):
    contract()
    state = live.inspect_state(backend, folder)['state']
    return result('inspect_state', 'observed', state=state, executionAuthorized=False)


def canonical_plan(state, batch, created):
    live.check_state(state)
    cat = p.catalogue()
    computational = live.computation_state(state)
    requests, batch_hash = p._batch(batch, computational, cat)
    fixture = cat['directions']['downlink']
    evaluations = {name: p._profile_issues(fixture['profiles'][name], requests, fixture) for name in MODES}
    possible = [name for name, reasons in evaluations.items() if not reasons]
    need(possible, 'no_compatible_live_profile', json.dumps(evaluations, ensure_ascii=False))
    mode = min(possible, key=lambda name: (sum(q['rate'] for q in fixture['profiles'][name]['queuePolicy'].values()),
                                          sum(q['ceil'] for q in fixture['profiles'][name]['queuePolicy'].values()), name))
    inner = p._build_plan(mode, requests, batch_hash, computational, cat, created)
    return p.seal({'policyPlanVersion': 'cnf-policy-live-plan-v1', 'liveStateHash': state['liveStateHash'],
                   'fingerprint': state['fingerprint'], 'adapterHash': live.adapter_hash(),
                   'contractHash': digest(contract()), 'batchHash': batch_hash, 'innerPlan': inner,
                   'computationState': computational, 'candidateEvaluations': evaluations,
                   'selectionRule': contract()['selectionRule'], 'validUntil': inner['validUntil']}, 'policyPlan')


def plan_policy(state, batch):
    try:
        return result('plan_policy', 'planned', plan=canonical_plan(state, batch, live.now()), executionAuthorized=False)
    except p.Rejection as error:
        return result('plan_policy', error.status, plan=None, issues=[{'code': error.code, 'detail': error.detail}], executionAuthorized=False)


def validate_plan(plan, batch, state, observation):
    p.check_seal(plan, 'policyPlan')
    live.check_state(state)
    need(digest(observation) == state['fingerprint'], 'live_state_drift', 'Observed sessions, resources or queue changed')
    checked = p.validate_plan(plan['innerPlan'], batch, plan['computationState'])
    if checked['status'] != 'valid_offline':
        issue = checked['issues'][0]
        raise p.Rejection(issue['code'], issue['detail'])
    expected = canonical_plan(state, batch, p.instant(plan['innerPlan']['createdAt']))
    need(plan == expected, 'policy_plan_mismatch', 'Plan, requests, whitelist selection or bound observed state differs')
    return result('validate_plan', 'valid_live_preconditions', policyPlanHash=plan['policyPlanHash'],
                  checkedAt=live.now().isoformat(), executionAuthorized=True,
                  authorizationScope='current_locked_single_policy_run_only')


def apply_plan(plan, batch, state, backend, folder):
    need(backend.locked, 'missing_lock', 'Hold the shared adapter lock throughout execution')
    need(not backend.mutation_attempted, 'already_executed', 'Use a new locked run and fresh observation for another request')
    folder.mkdir()
    observation = backend.observe(folder / 'recheck')
    try:
        validation = validate_plan(plan, batch, state, observation)
    except Exception as error:
        save(folder / 'rejection.json', {'error': str(error), 'errorCode': getattr(error, 'code', type(error).__name__),
                                       'checkedAt': live.now().isoformat(), 'policyPlanHash': plan['policyPlanHash']})
        raise
    save(folder / 'validation.json', validation)
    before = backend.tc()
    live.check_queue(before, 'none')
    save(folder / 'tc-before.json', before)
    mode = plan['innerPlan']['mode']
    save(folder / 'write-intent.json', {'policyPlanHash': plan['policyPlanHash'], 'endpoint': observation['endpoint'],
                                       'initialQueueMode': 'none', 'targetMode': mode, 'startedAt': live.now().isoformat()})
    need(live.now() <= p.instant(plan['validUntil']), 'expired_plan', 'Plan expired before actual write')
    backend.applied_mode = mode
    backend.mutation_attempted = True
    backend.write_batch(live.c.tc_commands(mode), folder)
    after = backend.tc()
    save(folder / 'tc-after.json', after)
    live.check_queue(after, mode)
    backend.apply_completed = True
    return result('apply_plan', 'applied', policyPlanHash=plan['policyPlanHash'], mode=mode,
                  completedAt=live.now().isoformat(), networkChanged=True)


def verify_plan(plan, batch, topology, folder):
    value = live.verify_plan({**plan, 'livePlanHash': plan['policyPlanHash']}, batch, topology, folder)
    value.pop('livePlanHash')
    return {**value, 'toolVersion': 'cnf-planner-policy-v1', 'policyPlanHash': plan['policyPlanHash']}


def restore(state, backend, folder):
    value = live.restore(state, backend, folder, mode=backend.applied_mode)
    return {**value, 'toolVersion': 'cnf-planner-policy-v1'}


def run(folder, batch, state=None, plan=None):
    need(sys.platform == 'linux', 'unsupported_platform', 'Execute on existing VM1 with its KUBECONFIG')
    need((state is None) == (plan is None), 'missing_bound_plan', 'Supply both state and plan or neither')
    batch, state, plan = copy.deepcopy((batch, state, plan))
    folder.mkdir(parents=True, exist_ok=False)
    live.archive_inputs(folder)
    save(folder / 'requests.json', copy.deepcopy(batch))
    overall = {'route': 'R2', 'stage': 'R2.S4', 'task': 'E3', 'outcome': 'failed', 'startedAt': live.now().isoformat(),
               'restoration': 'not_required_yet', 'finalStrictBaselinePassed': False,
               'requestInput': 'caller_supplied_exact_identifiers_and_targets'}
    try:
        with Backend() as backend:
            initial = inspect_state(backend, folder / 'initial')['state']
            save(folder / 'initial-state.json', initial)
            if state is None:
                state = initial
            save(folder / 'live-state.json', state)
            planning = result('plan_policy', 'planned', plan=plan, executionAuthorized=False) if plan is not None else plan_policy(state, batch)
            save(folder / 'planning.json', planning)
            plan = planning['plan']
            error = None
            try:
                if plan is not None:
                    save(folder / 'plan.json', plan)
                    executed = apply_plan(plan, batch, state, backend, folder / 'execution')
                    save(folder / 'execution.json', executed)
                    live.capture(backend, state, folder / 'measurement', mode=backend.applied_mode)
                    verified = verify_plan(plan, batch, backend.topology, folder / 'measurement')
                    save(folder / 'verification.json', verified)
                    need(verified['status'] == 'passed_controlled_targets', 'target_failure', 'Actual receiver targets failed')
            except Exception as caught:
                error = caught
                save(folder / 'execution-error.json', {'error': str(caught), 'errorCode': getattr(caught, 'code', type(caught).__name__),
                     'mutationAttempted': backend.mutation_attempted, 'applyCompleted': backend.apply_completed})
            finally:
                overall.update(mutationAttempted=backend.mutation_attempted, applyCompleted=backend.apply_completed)
                if backend.mutation_attempted:
                    try:
                        restored = restore(initial, backend, folder / 'restore')
                        save(folder / 'restoration.json', restored)
                        overall['restoration'] = restored['status']
                    except Exception as caught:
                        overall['restoration'] = 'failed: ' + str(caught)
                        save(folder / 'restoration.json', result('restore', 'failed', error=str(caught)))
                        raise
            final = backend.full(folder / 'final')
            need(digest(final) == initial['fingerprint'], 'final_state_drift', 'Final strict baseline differs from this run initial state')
            overall.update(finalStrictBaselinePassed=True, original26PodIdentitiesUnchanged=True)
            if error is not None:
                raise error
            if plan is None:
                overall.update(outcome=planning['status'], issues=planning['issues'])
            else:
                overall.update(outcome='passed_controlled_targets', selectedMode=plan['innerPlan']['mode'], policyPlanId=plan['policyPlanId'])
    except Exception as error:
        overall.update(error=str(error), errorCode=getattr(error, 'code', type(error).__name__))
        raise
    finally:
        overall['endedAt'] = live.now().isoformat()
        save(folder / 'summary.json', overall)
        print(json.dumps(overall, ensure_ascii=False), flush=True)
    return overall


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['inspect', 'plan', 'validate', 'run'])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--requests', type=Path)
    parser.add_argument('--state', type=Path)
    parser.add_argument('--plan', type=Path)
    args = parser.parse_args()
    if args.command == 'inspect':
        args.out.mkdir(parents=True, exist_ok=False)
        live.archive_inputs(args.out)
        with Backend() as backend:
            value = inspect_state(backend, args.out / 'live')
            save(args.out / 'state.json', value['state'])
            print(json.dumps({'status': value['status'], 'liveStateId': value['state']['liveStateId']}), flush=True)
    else:
        need(args.requests is not None, 'missing_requests', 'Supply explicit --requests JSON')
        batch = read(args.requests)
        state = unwrap(read(args.state), 'state') if args.state else None
        plan = unwrap(read(args.plan), 'plan') if args.plan else None
        if args.command == 'run':
            run(args.out, batch, state, plan)
        else:
            need(state is not None, 'missing_state', 'Supply the real inspect state')
            if args.command == 'plan':
                value = plan_policy(state, batch)
            else:
                need(plan is not None, 'missing_plan', 'Supply the bound policy plan')
                args.out.parent.mkdir(parents=True, exist_ok=True)
                with Backend() as backend:
                    observed = backend.full(args.out.with_name(args.out.stem + '-live-check'))
                    value = validate_plan(plan, batch, state, observed)
            need(not args.out.exists(), 'output_exists', 'Use a new output file')
            save(args.out, value)
            print(json.dumps({'status': value['status']}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('E3 ERROR: ' + str(error), file=sys.stderr)
        sys.exit(1)
