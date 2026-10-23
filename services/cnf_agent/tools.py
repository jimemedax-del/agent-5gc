"""Tool port and an in-memory simulator; never imports deployment scripts."""
from datetime import datetime, timedelta
from typing import Literal, Protocol

from .schemas import (
    Binding, BindService, Capability, Check, LimitBandwidth, Observation,
    OperationResult, Plan, Preview, Requirement, fingerprint,
)


class NetworkTools(Protocol):
    def inspect_network(self, at: datetime) -> Observation: ...
    def preview_policy(self, requirement: Requirement, version: int,
                       observation: Observation, at: datetime) -> Preview: ...
    def execute_experiment(self, operation_id: str, plan: Plan) -> OperationResult: ...
    def get_operation_result(self, operation_id: str) -> OperationResult | None: ...


Fault = Literal['none', 'inspect_error', 'execute_error', 'response_lost', 'result_unknown',
                'target_unmet', 'restore_failed']


class SimulatedTools:
    """A declared test capability, not evidence of available CNF/network features."""
    adapter_version = 'f1-simulator-v1'

    def __init__(self, fault: Fault = 'none', capacity_mbps: float = 10):
        self.fault = fault
        self.capacity_mbps = capacity_mbps
        self.operations: dict[str, OperationResult] = {}
        self.execution_count = 0

    def inspect_network(self, at: datetime) -> Observation:
        if self.fault == 'inspect_error':
            raise RuntimeError('Simulated inspection error')
        return Observation(
            ref='sim-observation-' + at.isoformat(), observed_at=at,
            expires_at=at + timedelta(seconds=120),
            capabilities=(Capability(id='service-access-with-cap', adapter_version=self.adapter_version,
                                     directions=('downlink',), destinations=('core', 'edge'),
                                     max_bandwidth_mbps=self.capacity_mbps),),
            bindings=tuple(Binding(user_id=user, session_id='sim-session-' + user) for user in ('ue1', 'ue2')),
        )

    def preview_policy(self, requirement: Requirement, version: int,
                       observation: Observation, at: datetime) -> Preview:
        binding = next((b for b in observation.bindings if b.user_id == requirement.user_id), None)
        capability = observation.capabilities[0] if observation.capabilities else None
        reason = None
        if binding is None:
            reason = '模拟现场没有该用户的会话绑定'
        elif requirement.missing() or requirement.conflicts():
            reason = '需求不完整或冲突'
        elif requirement.min_bandwidth_mbps is not None or requirement.max_latency_ms is not None or requirement.max_loss_percent is not None:
            reason = '模拟能力只支持服务接入与带宽上限，不支持保底、时延或丢包承诺'
        elif capability is None or requirement.direction not in capability.directions or requirement.destination not in capability.destinations:
            reason = '模拟目录不支持所需方向或目的地'
        elif requirement.max_bandwidth_mbps > capability.max_bandwidth_mbps:
            reason = '请求超过模拟能力范围'
        if reason:
            return Preview(status='rejected', reason=reason)
        plan = Plan(
            ref='sim-plan-' + fingerprint(requirement)[:16] + '-' + str(version),
            requirement_ref=fingerprint(requirement), requirement_version=version,
            observation_ref=observation.ref, session_id=binding.session_id,
            adapter_version=self.adapter_version,
            expires_at=min(at + timedelta(seconds=30), observation.expires_at),
            actions=(BindService(user_id=requirement.user_id, service_id=requirement.service_id,
                                 destination=requirement.destination),
                     LimitBandwidth(user_id=requirement.user_id, service_id=requirement.service_id,
                                    direction=requirement.direction, max_mbps=requirement.max_bandwidth_mbps)),
        )
        return Preview(status='planned', plan=plan, reason='仅生成模拟方案；没有配置真实网络')

    def execute_experiment(self, operation_id: str, plan: Plan) -> OperationResult:
        if operation_id in self.operations:
            return self.operations[operation_id]
        self.execution_count += 1
        if self.fault == 'result_unknown':
            raise TimeoutError('Simulated uncertain execution')
        failed = self.fault in ('execute_error', 'target_unmet', 'restore_failed')
        checks = tuple(Check(name=action.kind, expected=action.model_dump_json(),
                             actual='模拟器返回值，无真实测量', passed=not failed) for action in plan.actions)
        result = OperationResult(
            operation_id=operation_id, plan_ref=plan.ref,
            outcome='failed' if self.fault == 'execute_error' else ('target_unmet' if failed else 'succeeded'),
            restoration='failed' if self.fault == 'restore_failed' else 'restored',
            checks=checks, detail='模拟执行与模拟恢复；未调用 SSH、kubectl、tc 或真实模型',
        )
        self.operations[operation_id] = result
        if self.fault == 'response_lost':
            raise TimeoutError('Simulated reply loss after completion')
        return result

    def get_operation_result(self, operation_id: str) -> OperationResult | None:
        return self.operations.get(operation_id)
