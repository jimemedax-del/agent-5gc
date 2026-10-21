#!/usr/bin/env python3
"""E1: deterministic offline planner and tool contracts for the C1/C2 fixtures."""
import argparse
import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / 'infra/cnfs/planner-contract.json'
REQUIREMENTS = ROOT / 'infra/cnfs/requirements-contract.json'
SLOTS = ('ue1', 'ue2')


class Rejection(ValueError):
    def __init__(self, code, detail, status='rejected'):
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


def need(condition, code, detail, status='rejected'):
    if not condition:
        raise Rejection(code, detail, status)


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate_json_key', key)
            result[key] = value
        return result

    def invalid(value):
        raise Rejection('nonfinite_json', value)
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=pairs, parse_constant=invalid)


def digest(value):
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        raise Rejection('non_json_value', 'Only finite, JSON-serializable contract values are accepted') from None
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def file_hash(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def keys(value, expected, name):
    need(type(value) is dict and set(value) == set(expected), 'invalid_fields', name + ': missing or unknown fields')


def identifier(value, name):
    need(isinstance(value, str) and 0 < len(value.strip()) <= 128, 'invalid_identifier', name)


def number(value, name, minimum=0):
    need(type(value) in [int, float] and Decimal(str(value)).is_finite() and value >= minimum,
         'invalid_number', name)
    return Decimal(str(value))


def instant(value):
    need(isinstance(value, str), 'invalid_time', 'Timezone-aware ISO timestamp required')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise Rejection('invalid_time', 'Invalid ISO timestamp') from None
    need(parsed.tzinfo is not None, 'invalid_time', 'Timestamp must include timezone')
    return parsed.astimezone(timezone.utc)


def clock(at):
    return datetime.now(timezone.utc) if at is None else instant(at)


def envelope(tool, status, **values):
    return {'toolVersion': 'cnf-planner-tools-v1', 'tool': tool, 'status': status,
            'executionAuthorized': False, 'networkChanged': False, 'issues': [], **values}


def failed(tool, error):
    return envelope(tool, error.status, issues=[{'code': error.code, 'detail': error.detail}])


def seal(value, kind):
    output = copy.deepcopy(value)
    checksum = digest(output)
    output[kind + 'Hash'] = checksum
    output[kind + 'Id'] = kind + '-' + checksum[:20]
    return output


def check_seal(value, kind):
    need(type(value) is dict and kind + 'Hash' in value and kind + 'Id' in value,
         'missing_hash', kind)
    body = {k: v for k, v in value.items() if k not in [kind + 'Hash', kind + 'Id']}
    need(seal(body, kind) == value, 'content_hash_mismatch', kind + ' content changed')
    return body


def catalogue():
    contract = read_json(CONTRACT)
    need(contract['contractVersion'] == 'cnf-planner-tools-v1' and contract['scope'] == 'offline_two_ue_udp_steady_mean_fixture',
         'unsupported_contract', 'Unknown planner contract')
    need(contract['candidateModes'] == ['equal', 'guarantee-ue1', 'guarantee-ue2', 'priority-ue1', 'priority-ue2'] and
         contract['maxStateTtlSeconds'] == 300 and contract['maxPlanAgeSeconds'] == 30,
         'unsupported_contract', 'Policy set or timing bounds changed')
    need(contract['requiredRequestFields'] == read_json(REQUIREMENTS)['requiredForPlanning'],
         'unsupported_contract', 'D-to-E required fields changed')
    references = {'downlink': ('infra/cnfs/contention-experiment.json', 'results/20261020-cnf-contention/metadata.json', 'cnf-contention-v1'),
                  'uplink': ('infra/cnfs/contention-uplink-experiment.json', 'results/20261020-cnf-contention-uplink/metadata.json', 'cnf-contention-uplink-v2')}
    inputs = [Path(__file__), CONTRACT, REQUIREMENTS]
    directions = {}
    for direction, (config_path, evidence_path, version) in references.items():
        need(contract['referenceFiles'][direction] == {'config': config_path, 'evidence': evidence_path},
             'unknown_reference', direction)
        config, evidence = read_json(ROOT / config_path), read_json(ROOT / evidence_path)
        need(config['configVersion'] == version and config['direction'] == direction and config['protocol'] == 'UDP' and
             config['capacityMbps'] == 10 and config['admissionMarginMbps'] == .5 and config['offeredMbpsPerUE'] == 8 and
             config['durationSeconds'] == 30 and config['steadyStartSeconds'] == 5 and config['steadyEndSeconds'] == 25 and
             config['modes'] == ['shared', *contract['candidateModes']] and [u['name'] for u in config['users']] == list(SLOTS),
             'fixture_drift', direction + ' fixture no longer matches E1 assumptions')
        need(evidence['configVersion'] == version and evidence['outcome'] == 'accepted-within-declared-scope' and
             evidence['formal']['networkAcceptancePassed'] is True and evidence['formal']['evidenceIntegrity'] == 'passed',
             'unaccepted_reference', direction)
        need(file_hash(ROOT / config_path) == evidence['evidence']['formalInputSha256LF'][config_path],
             'reference_config_hash_mismatch', direction + ' configuration differs from the accepted experiment')
        policy_source = 'scripts/cnf-contention.py' if direction == 'downlink' else 'scripts/cnf-contention-uplink.py'
        need(file_hash(ROOT / policy_source) == evidence['evidence']['formalInputSha256LF'][policy_source],
             'reference_adapter_hash_mismatch', direction + ' fixed-policy source drifted')
        limits = config['acceptance']
        need((limits['equalMinimumMbpsPerUE'], limits['guaranteedHighMinimumMbps'], limits['guaranteedLowMinimumMbps'],
              limits['priorityHighMinimumMbps'], limits['priorityLowMinimumMbps']) == (4, 5.5, 1.7, 6, 1.7),
             'reference_threshold_drift', direction)
        profiles = {}
        for mode in contract['candidateModes']:
            high = mode.split('-')[-1]
            queue, floors = {}, {}
            for slot in SLOTS:
                if mode == 'equal':
                    queue[slot] = {'rate': 4, 'ceil': 8, 'prio': 0}
                    floors[slot] = 4
                elif mode.startswith('guarantee'):
                    queue[slot] = {'rate': 6 if slot == high else 2, 'ceil': 8 if slot == high else 4,
                                   'prio': 0 if slot == high else 1}
                    floors[slot] = 5.5 if slot == high else 1.7
                else:
                    queue[slot] = {'rate': 2, 'ceil': 10, 'prio': 0 if slot == high else 1}
                    # Observed priority throughput is borrowed capacity, not a hard 6 Mbps reservation.
                    floors[slot] = 1.7
            profiles[mode] = {'queuePolicy': queue, 'referenceMinMbps': floors}
        directions[direction] = {'configVersion': version, 'namespace': config['namespace'], 'deployment': config['deployment'],
                                 'queueDevice': config.get('queueDevice', 'eth0'), 'capacityMbps': 10,
                                 'admissionMarginMbps': .5, 'durationSeconds': 30, 'windowSeconds': 20,
                                 'offeredMbpsPerUE': 8, 'profiles': profiles, 'evidenceRef': evidence_path}
        inputs += [ROOT / config_path, ROOT / evidence_path, ROOT / policy_source]
    body = {'contract': contract, 'directions': directions,
            'sourceSha256LF': {p.relative_to(ROOT).as_posix(): file_hash(p) for p in inputs}}
    return {**body, 'catalogHash': digest(body)}


def capabilities():
    return envelope('capabilities', 'ok', scope='offline_only', contract=catalogue()['contract'])


def _state(source, cat, now):
    keys(source, ['sourceVersion', 'mode', 'direction', 'declaredAt', 'ttlSeconds', 'capacityMbps',
                  'unmanagedReservedMbps', 'unmanagedTrafficDetected', 'nativeQosEnabled', 'activeProfile', 'bindings'], 'state source')
    need(source['sourceVersion'] == 'cnf-planner-state-input-v1' and source['mode'] == 'declared_fixture',
         'unsupported_state_source', 'E1 only inspects declared offline fixtures, not live observations')
    need(isinstance(source['direction'], str) and source['direction'] in cat['directions'],
         'unsupported_direction', 'Use one independent uplink or downlink fixture')
    fixture = cat['directions'][source['direction']]
    number(source['capacityMbps'], 'capacityMbps')
    need(source['capacityMbps'] == fixture['capacityMbps'], 'fixture_capacity_drift', 'Historical fixture root is 10 Mbps')
    need(type(source['ttlSeconds']) is int and 0 < source['ttlSeconds'] <= cat['contract']['maxStateTtlSeconds'],
         'invalid_state_ttl', 'TTL must be 1..300 seconds')
    created = instant(source['declaredAt'])
    expiry = created + timedelta(seconds=source['ttlSeconds'])
    need(created <= now < expiry, 'stale_state', 'Fixture declaration is future-dated or expired')
    number(source['unmanagedReservedMbps'], 'unmanagedReservedMbps')
    need(type(source['unmanagedTrafficDetected']) is bool and type(source['nativeQosEnabled']) is bool,
         'invalid_state_flag', 'Traffic and native QoS flags must be boolean')
    need(source['unmanagedReservedMbps'] == 0 and source['unmanagedTrafficDetected'] is False,
         'unmanaged_competition_unvalidated', 'Reference envelopes do not cover additional competing traffic or reservations')
    need(source['nativeQosEnabled'] is False, 'native_qos_unaccepted', 'B native UPF QoS-on is not accepted')
    need(source['activeProfile'] in ['none', 'shared', *fixture['profiles']], 'unknown_existing_policy', 'Refuse unknown policy ownership')
    bindings = source['bindings']
    need(type(bindings) is list and len(bindings) == 2, 'unsupported_user_count', 'Exactly two declared fixture bindings required')
    for binding in bindings:
        keys(binding, ['slot', 'user_id', 'session_id'], 'binding')
        need(binding['slot'] in SLOTS, 'unknown_slot', 'Unknown fixture slot')
        identifier(binding['user_id'], 'user_id')
        identifier(binding['session_id'], 'session_id')
    for field in ['slot', 'user_id', 'session_id']:
        need(len({b[field] for b in bindings}) == 2, 'duplicate_binding', field)
    return seal({'snapshotVersion': 'cnf-planner-state-v1', 'source': copy.deepcopy(source),
                 'validUntil': expiry.isoformat(), 'catalogHash': cat['catalogHash'],
                 'sourceHash': digest(source), 'liveBindingsVerified': False, 'liveStateObserved': False}, 'state')


def inspect_state(source, at=None):
    try:
        state = _state(source, catalogue(), clock(at))
        return envelope('inspect_state', 'ok', state=state)
    except Rejection as error:
        return failed('inspect_state', error)


def _check_state(state, cat, now):
    body = check_seal(state, 'state')
    keys(body, ['snapshotVersion', 'source', 'validUntil', 'catalogHash', 'sourceHash',
                'liveBindingsVerified', 'liveStateObserved'], 'state snapshot')
    need(state['catalogHash'] == cat['catalogHash'], 'catalog_drift', 'Catalogue/code/evidence changed since state snapshot')
    need(_state(state['source'], cat, now) == state, 'state_drift', 'State snapshot is not a canonical fixture declaration')


def _batch(batch, state, cat):
    keys(batch, ['requestVersion', 'batch_id', 'assessmentScope', 'protocol', 'requests'], 'request batch')
    need(batch['requestVersion'] == 'cnf-planner-requests-v1', 'unsupported_request_version', 'Unknown request contract')
    identifier(batch['batch_id'], 'batch_id')
    need(batch['assessmentScope'] == 'controlled_receiver_steady_mean', 'unsupported_assessment_scope', 'E1 is not a general or continuous SLA planner')
    need(batch['protocol'] == 'UDP', 'unsupported_transport', 'Reference experiments use UDP')
    requests = batch['requests']
    need(type(requests) is list and len(requests) == 2, 'unsupported_user_count', 'Two concurrent fixture requests required')
    schema = read_json(REQUIREMENTS)
    bindings = {b['user_id']: b for b in state['source']['bindings']}
    fixture = cat['directions'][state['source']['direction']]
    by_slot, ids = {}, set()
    for request in requests:
        keys(request, ['request_id', 'user_id', 'session_id', 'requirements'], 'request')
        for field in ['request_id', 'user_id', 'session_id']:
            if field != 'request_id':
                need(request[field] is not None, 'missing_subject', request['request_id'] + ': ' + field,
                     'needs_clarification')
            identifier(request[field], field)
        need(request['request_id'] not in ids, 'duplicate_request', 'Repeated request id')
        ids.add(request['request_id'])
        need(request['user_id'] in bindings, 'unknown_user', request['user_id'])
        binding = bindings[request['user_id']]
        need(request['session_id'] == binding['session_id'], 'session_binding_mismatch', request['user_id'])
        need(binding['slot'] not in by_slot, 'duplicate_user', 'One request per user in this batch')
        fields = request['requirements']
        need(type(fields) is dict and set(fields) <= set(schema['fields']), 'unknown_target', 'Only versioned requirement fields are accepted')
        missing = [f for f in cat['contract']['requiredRequestFields'] if fields.get(f) is None]
        need(not missing, 'missing_required_targets', request['request_id'] + ': ' + ', '.join(missing), 'needs_clarification')
        for name, value in fields.items():
            if value is None:
                continue
            rule = schema['fields'][name]
            if rule['type'] == 'enum':
                need(value in rule['values'], 'invalid_target', name)
            else:
                number(value, name)
                need(('exclusiveMinimum' not in rule or value > rule['exclusiveMinimum']) and
                     ('maximum' not in rule or value <= rule['maximum']), 'invalid_target', name)
        need(fields['min_bandwidth_mbps'] <= fields['max_bandwidth_mbps'], 'conflicting_bounds', request['request_id'])
        need(fields['direction'] == state['source']['direction'], 'unsupported_direction', 'Batch and fixture must share one direction')
        need(fields['valid_for_seconds'] == fixture['durationSeconds'] and fields['measurement_window_seconds'] == fixture['windowSeconds'],
             'unsupported_time_scope', 'E1 references 30 s experiments and the 20 s steady window only')
        for name in ['rtt_p95_max_ms', 'one_way_p95_max_ms', 'loss_max_percent', 'deadline_seconds']:
            need(fields.get(name) is None, 'unsupported_target', name + ' is not an accepted fixture guarantee')
        by_slot[binding['slot']] = request
    normalized = {**batch, 'requests': sorted(copy.deepcopy(requests), key=lambda r: r['request_id'])}
    return by_slot, digest(normalized)


def _profile_issues(profile, requests, fixture):
    reasons = []
    total = sum(number(r['requirements']['min_bandwidth_mbps'], 'minimum') for r in requests.values())
    margin = Decimal(str(fixture['admissionMarginMbps']))
    if total + margin > fixture['capacityMbps']:
        reasons.append('requested_minima_exceed_capacity_with_margin')
    reserved = sum(p['rate'] for p in profile['queuePolicy'].values())
    if Decimal(str(reserved)) + margin > fixture['capacityMbps']:
        reasons.append('queue_reservations_exceed_capacity_with_margin')
    for slot, request in requests.items():
        targets = request['requirements']
        if targets['min_bandwidth_mbps'] > profile['referenceMinMbps'][slot]:
            reasons.append(slot + ':minimum_exceeds_reference_envelope')
        if profile['queuePolicy'][slot]['ceil'] > targets['max_bandwidth_mbps']:
            reasons.append(slot + ':queue_ceiling_exceeds_requested_cap')
    rank = {'low': 0, 'normal': 1, 'high': 2}
    difference = rank[requests['ue1']['requirements']['priority']] - rank[requests['ue2']['requirements']['priority']]
    queue_difference = profile['queuePolicy']['ue2']['prio'] - profile['queuePolicy']['ue1']['prio']
    if (difference == 0 and queue_difference != 0) or difference * queue_difference < 0 or (difference != 0 and queue_difference == 0):
        reasons.append('relative_priority_not_preserved')
    return reasons


def _build_plan(mode, requests, batch_hash, state, cat, created):
    direction = state['source']['direction']
    fixture = cat['directions'][direction]
    latest_start = min(created + timedelta(seconds=cat['contract']['maxPlanAgeSeconds']),
                       instant(state['validUntil']) - timedelta(seconds=fixture['durationSeconds']))
    need(latest_start >= created, 'insufficient_state_horizon', 'State must remain valid for the full 30 s fixture run')
    profile = fixture['profiles'][mode]
    allocation = []
    for slot in SLOTS:
        request = requests[slot]
        allocation.append({'slot': slot, 'request_id': request['request_id'], 'user_id': request['user_id'],
                           'session_id': request['session_id'], 'queue': profile['queuePolicy'][slot],
                           'requestedMinMbps': request['requirements']['min_bandwidth_mbps'],
                           'requestedMaxMbps': request['requirements']['max_bandwidth_mbps'],
                           'referenceMinMbps': profile['referenceMinMbps'][slot]})
    return seal({'planVersion': 'cnf-policy-plan-v1', 'scope': 'offline_two_ue_udp_steady_mean_fixture',
                 'createdAt': created.isoformat(), 'validUntil': latest_start.isoformat(),
                 'stateHash': state['stateHash'], 'batchHash': batch_hash, 'catalogHash': cat['catalogHash'],
                 'direction': direction, 'mode': mode, 'allocations': allocation,
                 'action': {'kind': 'select_existing_fixture_mode', 'namespace': fixture['namespace'],
                            'deployment': fixture['deployment'], 'queueDevice': fixture['queueDevice'],
                            'configVersion': fixture['configVersion'], 'mode': mode},
                 'restoreTarget': {'mode': state['source']['activeProfile'], 'stateHash': state['stateHash']},
                 'verificationTarget': {'metric': 'receiver_steady_mean_mbps', 'steadyStartSeconds': 5, 'steadyEndSeconds': 25,
                                        'durationSeconds': 30, 'offeredMbpsPerUE': 8, 'evidenceRef': fixture['evidenceRef']},
                 'liveExecutionSupported': False, 'liveGuaranteeClaimed': False}, 'plan')


def plan_policy(batch, state, at=None):
    try:
        cat, now = catalogue(), clock(at)
        _check_state(state, cat, now)
        requests, batch_hash = _batch(batch, state, cat)
        fixture = cat['directions'][state['source']['direction']]
        evaluations = {mode: _profile_issues(profile, requests, fixture) for mode, profile in fixture['profiles'].items()}
        possible = [mode for mode, reasons in evaluations.items() if not reasons]
        if not possible:
            return envelope('plan_policy', 'rejected', plan=None, planGenerated=False,
                            issues=[{'code': 'no_compatible_validated_profile', 'detail': 'No fixed reference profile satisfies the entire batch'}],
                            candidateEvaluations=evaluations)
        mode = min(possible, key=lambda name: (sum(q['rate'] for q in fixture['profiles'][name]['queuePolicy'].values()),
                                              sum(q['ceil'] for q in fixture['profiles'][name]['queuePolicy'].values()), name))
        plan = _build_plan(mode, requests, batch_hash, state, cat, now)
        return envelope('plan_policy', 'planned', plan=plan, planGenerated=True, candidateEvaluations=evaluations,
                        selectionRule='minimum_total_queue_rate_then_ceiling_then_mode_name_within_feasible_whitelist')
    except Rejection as error:
        return {**failed('plan_policy', error), 'plan': None, 'planGenerated': False}


def validate_plan(plan, batch, state, at=None):
    try:
        cat, now = catalogue(), clock(at)
        _check_state(state, cat, now)
        requests, batch_hash = _batch(batch, state, cat)
        body = check_seal(plan, 'plan')
        keys(body, ['planVersion', 'scope', 'createdAt', 'validUntil', 'stateHash', 'batchHash', 'catalogHash',
                    'direction', 'mode', 'allocations', 'action', 'restoreTarget', 'verificationTarget',
                    'liveExecutionSupported', 'liveGuaranteeClaimed'], 'plan')
        need(plan.get('planVersion') == 'cnf-policy-plan-v1' and plan.get('stateHash') == state['stateHash'] and
             plan.get('batchHash') == batch_hash and plan.get('catalogHash') == cat['catalogHash'],
             'plan_binding_mismatch', 'Plan belongs to different requests, state or catalogue')
        need(instant(plan['createdAt']) <= now <= instant(plan['validUntil']), 'expired_plan', 'Plan is expired or future-dated')
        fixture = cat['directions'][state['source']['direction']]
        need(isinstance(plan.get('mode'), str) and plan['mode'] in fixture['profiles'],
             'unknown_policy_mode', 'Only declared fixed modes are allowed')
        reasons = _profile_issues(fixture['profiles'][plan['mode']], requests, fixture)
        need(not reasons, 'policy_constraints_failed', ', '.join(reasons))
        expected = _build_plan(plan['mode'], requests, batch_hash, state, cat, instant(plan['createdAt']))
        need(plan == expected, 'plan_semantics_mismatch', 'Policy, targets, identity or recovery descriptor differs from canonical contract')
        need(now + timedelta(seconds=fixture['durationSeconds']) <= instant(state['validUntil']),
             'insufficient_state_horizon', 'Full run no longer fits state freshness horizon')
        return envelope('validate_plan', 'valid_offline', validForOfflineReview=True,
                        validation={'planHash': plan['planHash'], 'batchHash': batch_hash, 'stateHash': state['stateHash'],
                                    'catalogHash': cat['catalogHash'], 'validUntil': plan['validUntil'],
                                    'liveExecutionSupported': False})
    except Rejection as error:
        return {**failed('validate_plan', error), 'validForOfflineReview': False}


def import_requirements(normalized, batch_id, protocol, assessment_scope):
    """Bridge D output without trusting its candidate/parsed flags or inventing missing values."""
    identifier(batch_id, 'batch_id')
    need(protocol == 'UDP' and assessment_scope == 'controlled_receiver_steady_mean',
         'unsupported_assessment_scope', 'Explicit transport and fixture assessment scope required')
    need(type(normalized) is list and normalized, 'invalid_normalized_input', 'Expected list of normalized requirements')
    items = []
    for entry in normalized:
        need(type(entry) is dict and entry.get('contractVersion') == 'cnf-requirements-v1',
             'unsupported_requirements_contract', 'Unknown D output')
        identifier(entry.get('request_id'), 'request_id')
        need(type(entry.get('requirements')) is dict and set(entry['requirements']) == set(read_json(REQUIREMENTS)['fields']),
             'invalid_normalized_target', 'D output must contain all versioned fields')
        need(type(entry.get('subject')) is dict and {'user_id', 'session_id'} <= set(entry['subject']),
             'invalid_normalized_subject', 'Missing D subject fields')
        values = {}
        for name, field in entry['requirements'].items():
            need(type(field) is dict and {'resolution', 'value'} <= set(field), 'invalid_normalized_target', name)
            need(field['resolution'] in ['explicit', 'unknown'], 'conflicting_normalized_target', name)
            need((field['resolution'] == 'unknown' and field['value'] is None) or
                 (field['resolution'] == 'explicit' and field['value'] is not None), 'invalid_normalized_target', name)
            values[name] = field['value']
        items.append({'request_id': entry['request_id'], 'user_id': entry['subject']['user_id'],
                      'session_id': entry['subject']['session_id'], 'requirements': values})
    return {'requestVersion': 'cnf-planner-requests-v1', 'batch_id': batch_id,
            'assessmentScope': assessment_scope, 'protocol': protocol, 'requests': items}


def unsupported(tool, **references):
    return envelope(tool, 'unsupported', issues=[{'code': 'live_adapter_not_implemented',
                    'detail': 'E1 provides offline planning only; no execution, measurements or recovery occurred'}],
                    supported=False, references=references)


def apply_plan(plan, validation, state):
    return unsupported('apply_plan', planId=plan.get('planId'), stateId=state.get('stateId'))


def verify_plan(plan, execution):
    return unsupported('verify_plan', planId=plan.get('planId'))


def restore(snapshot, execution):
    return unsupported('restore', stateId=snapshot.get('stateId'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ['capabilities', 'inspect', 'plan', 'validate', 'apply', 'verify', 'restore', 'import-requirements']:
        child = sub.add_parser(name)
        child.add_argument('--out', type=Path, required=True)
        child.add_argument('--at', help='Offline simulation clock only; never establishes live freshness')
        if name == 'inspect':
            child.add_argument('--source', type=Path, required=True)
        if name in ['plan', 'validate']:
            child.add_argument('--requests', type=Path, required=True)
            child.add_argument('--state', type=Path, required=True)
        if name == 'validate':
            child.add_argument('--plan', type=Path, required=True)
        if name == 'import-requirements':
            child.add_argument('--input', type=Path, required=True)
            child.add_argument('--batch-id', required=True)
            child.add_argument('--protocol', required=True)
            child.add_argument('--assessment-scope', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'capabilities':
            result = capabilities()
        elif args.command == 'inspect':
            result = inspect_state(read_json(args.source), args.at)
        elif args.command == 'import-requirements':
            result = envelope('import_requirements', 'ok', requests=import_requirements(read_json(args.input), args.batch_id,
                               args.protocol, args.assessment_scope))
        elif args.command in ['plan', 'validate']:
            requests = read_json(args.requests)
            if type(requests) is dict and requests.get('tool') == 'import_requirements':
                need(requests.get('status') == 'ok' and type(requests.get('requests')) is dict,
                     'invalid_import_result', 'Requirements import did not produce a request batch')
                requests = requests['requests']
            state = read_json(args.state)
            need(type(state) is dict, 'invalid_state_input', 'State JSON object required')
            state = state.get('state', state)
            if args.command == 'plan':
                result = plan_policy(requests, state, args.at)
            else:
                plan = read_json(args.plan)
                need(type(plan) is dict, 'invalid_plan_input', 'Plan JSON object required')
                result = validate_plan(plan.get('plan', plan), requests, state, args.at)
        else:
            result = unsupported({'apply': 'apply_plan', 'verify': 'verify_plan', 'restore': 'restore'}[args.command])
    except (Rejection, OSError, json.JSONDecodeError) as error:
        result = failed(args.command, error if isinstance(error, Rejection) else Rejection('invalid_input', str(error)))
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'tool': result['tool'], 'status': result['status'], 'executionAuthorized': False}))


if __name__ == '__main__':
    main()
