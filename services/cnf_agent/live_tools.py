"""Thin Agent port over the existing E3 planner and transaction executor."""
from datetime import datetime
import hashlib
import json
import math

from .live_contracts import LiveObservation, LivePlan, LivePreview, OperationSnapshot, PolicyExtraction
from .schemas import Check, OperationResult, fingerprint


class LiveTools:
    def __init__(self, transport):
        self.transport = transport

    def inspect_network(self):
        if self.transport.release_id is None:
            self.transport.deploy()
        state = self.transport.call('inspect', {})
        return LiveObservation(ref=state['liveStateHash'], observed_at=datetime.fromisoformat(state['observedAt']),
                               expires_at=datetime.fromisoformat(state['validUntil']),
                               release_id=self.transport.release_id, state=state)

    @staticmethod
    def batch(requirement: PolicyExtraction, observation: LiveObservation, version: int):
        requests = []
        for goal in sorted(requirement.requests, key=lambda g: g.user_slot):
            binding = observation.state['observation']['bindings'][goal.user_slot]
            requests.append({'request_id': f'agent-v{version}-{goal.user_slot}',
                             'user_id': binding['supi'], 'session_id': binding['session_id'],
                             'requirements': {key: getattr(goal, key) for key in (
                                 'direction', 'min_bandwidth_mbps', 'max_bandwidth_mbps', 'priority',
                                 'valid_for_seconds', 'measurement_window_seconds')}})
        return {'requestVersion': 'cnf-planner-requests-v1',
                'batch_id': 'agent-' + fingerprint(requirement)[:20] + '-v' + str(version),
                'assessmentScope': 'controlled_receiver_steady_mean', 'protocol': 'UDP', 'requests': requests}

    def preview_policy(self, requirement, version, observation):
        batch = self.batch(requirement, observation, version)
        value = self.transport.call('preview', {'state': observation.state, 'batch': batch})
        if value['status'] != 'planned':
            return LivePreview(status=value['status'], detail='; '.join(i['code'] for i in value['issues']))
        policy = value['plan']
        plan = LivePlan(ref=policy['policyPlanHash'], requirement_ref=fingerprint(requirement),
                        requirement_version=version, observation_ref=observation.ref,
                        selected_mode=policy['innerPlan']['mode'], expires_at=datetime.fromisoformat(policy['validUntil']),
                        release_id=observation.release_id, batch=batch, policy=policy)
        return LivePreview(status='planned', plan=plan, detail='E3 受控下行方案；实际写入前仍需锁内复验')

    def execute_experiment(self, operation_id, plan, observation):
        value = self.transport.call('execute', {'operation_id': operation_id, 'state': observation.state,
                                    'batch': plan.batch, 'plan': plan.policy})
        return OperationSnapshot.model_validate(value)

    def get_operation_result(self, operation_id):
        return OperationSnapshot.model_validate(self.transport.call('result', {'operation_id': operation_id}))


def operation_result(snapshot: OperationSnapshot, plan: LivePlan, observation: LiveObservation):
    raw = snapshot.result
    if raw is None:
        return None
    payload = {'state': observation.state, 'batch': plan.batch, 'plan': plan.policy}
    expected = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if raw.get('payload_hash') != expected or raw.get('plan_ref') != plan.ref:
        raise ValueError('Result belongs to a different bound transaction')
    summary, verification = raw.get('summary', {}), raw.get('verification') or {}
    if not isinstance(summary, dict) or not isinstance(verification, dict):
        raise ValueError('Invalid execution result structure')
    restored = summary.get('finalStrictBaselinePassed') is True and (
        summary.get('restoration') == 'restored' or
        (summary.get('mutationAttempted') is False and summary.get('restoration') == 'not_required_yet'))
    comparisons = verification.get('targetComparisons', {})
    if not isinstance(comparisons, dict) or not set(comparisons) <= {'ue1', 'ue2'}:
        raise ValueError('Invalid target comparison structure')
    demands = {request['user_id']: request['requirements'] for request in plan.batch['requests']}
    checks = []
    for slot, value in sorted(comparisons.items()):
        binding = observation.state['observation']['bindings'][slot]
        demand = demands[binding['supi']]
        if value['minimumMbps'] != demand['min_bandwidth_mbps'] or value['maximumMbps'] != demand['max_bandwidth_mbps']:
            raise ValueError('Measured target bounds differ from the requested bounds')
        rate = value['receivedSteadyMbps']
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate):
            raise ValueError('Invalid measured receiver rate')
        passed = demand['min_bandwidth_mbps'] <= rate <= demand['max_bandwidth_mbps'] and value['passed'] is True
        checks.append(Check(name=slot, expected=f"{demand['min_bandwidth_mbps']}–{demand['max_bandwidth_mbps']} Mbps",
                            actual=str(rate) + ' Mbps', passed=passed))
    success = (summary.get('outcome') == 'passed_controlled_targets' and restored
               and raw.get('error_code') is None and summary.get('errorCode') is None
               and summary.get('mutationAttempted') is True and summary.get('applyCompleted') is True
               and summary.get('original26PodIdentitiesUnchanged') is True
               and summary.get('selectedMode') == plan.selected_mode
               and summary.get('policyPlanId') == plan.policy['policyPlanId']
               and verification.get('policyPlanHash') == plan.ref
               and verification.get('status') == 'passed_controlled_targets'
               and verification.get('evidenceIntegrity') == 'passed'
               and isinstance(verification.get('historicalFixtureChecks'), dict)
               and verification['historicalFixtureChecks'].get('passed') is True
               and verification.get('latencyLossGuaranteesAccepted') is False
               and set(comparisons) == {'ue1', 'ue2'} and all(c.passed for c in checks))
    restoration = 'restored' if restored else ('failed' if str(summary.get('restoration', '')).startswith('failed') else 'unknown')
    outcome = 'succeeded' if success else ('unknown' if summary.get('outcome') == 'unknown' else 'failed')
    error_code = raw.get('error_code') or summary.get('errorCode')
    detail = '仅 C1 双 UE 下行受控接收均值；无原生 5G QoS 或时延/丢包保障结论'
    if error_code:
        detail += '；执行错误：' + str(error_code)
    return OperationResult(operation_id=snapshot.operation_id, mode='live', plan_ref=plan.ref,
                           outcome=outcome, restoration=restoration, checks=tuple(checks), evidence=snapshot.evidence,
                           detail=detail)
