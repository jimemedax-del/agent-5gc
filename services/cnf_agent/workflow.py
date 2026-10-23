"""Explicit offline state machine. Only the tool port can simulate an action."""
from datetime import datetime, timezone
import time
from uuid import uuid4

from .memory import Memory
from .provider import ModelProvider, ProviderCallError
from .schemas import (
    BindService, Budget, Extraction, LimitBandwidth, Message, ModelReply, ModelUsage, Observation,
    OperationResult, Phase, Plan, Preview, Session, Usage, fingerprint,
)
from .tools import NetworkTools


FIELD_LABELS = {
    'user_id': '用户对象', 'service_id': '业务服务', 'direction': '传输方向',
    'destination': '中心或边缘目的地', 'max_bandwidth_mbps': '明确的带宽上限',
}


class StopRun(Exception):
    def __init__(self, session: Session):
        self.session = session


class CallFailed(Exception):
    def __init__(self, session: Session):
        self.session = session


class Workflow:
    def __init__(self, memory: Memory, provider: ModelProvider, tools: NetworkTools,
                 now=None, monotonic=None):
        self.memory = memory
        self.provider = provider
        self.tools = tools
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.monotonic = monotonic or time.monotonic

    def create(self, budget: Budget | None = None) -> Session:
        return self.memory.create(Session(id=uuid4().hex, budget=budget or Budget()))

    def _save(self, session: Session, kind: str, **changes) -> Session:
        return self.memory.save(session.model_copy(update=changes), kind)

    def _stop(self, session: Session, phase: Phase, *notes: str):
        raise StopRun(self._save(session, phase.value, phase=phase, notes=tuple(notes)))

    def _call(self, session: Session, phase: Phase, name: str, counter: str, function,
              operation_id: str | None = None):
        usage = session.usage.model_dump()
        usage['steps'] += 1
        usage[counter] += 1
        limits = session.budget.model_dump()
        if any(value > limits[key] for key, value in usage.items()) or usage['tokens'] >= limits['tokens']:
            self._stop(session, Phase.BUDGET, '会话预算耗尽；未启动下一次调用')
        prepared = session.model_copy(update={'phase': phase, 'usage': Usage(**usage)})
        if operation_id:
            prepared = prepared.model_copy(update={'operation_id': operation_id})
        session = self.memory.save(prepared, name + '_started', claim_operation=bool(operation_id))
        started = self.monotonic()
        try:
            result = function()
        except Exception as error:
            elapsed = max(0, self.monotonic() - started)
            tokens = error.tokens if isinstance(error, ProviderCallError) else 0
            session = self._save(
                session, name + '_error',
                usage=session.usage.model_copy(update={'elapsed_seconds': session.usage.elapsed_seconds + elapsed,
                                                       'tokens': session.usage.tokens + tokens}),
                notes=(f'{name} 调用失败：{str(error) if isinstance(error, ProviderCallError) else type(error).__name__}',))
            if isinstance(error, ProviderCallError):
                session = self.memory.save(session, 'provider_error_usage',
                                           {'tokens': tokens, 'metadata': error.metadata})
            raise CallFailed(session) from error
        elapsed = max(0, self.monotonic() - started)
        usage = session.usage.model_copy(update={'elapsed_seconds': session.usage.elapsed_seconds + elapsed})
        if isinstance(result, ModelUsage):
            usage = usage.model_copy(update={'tokens': usage.tokens + result.tokens})
        session = self.memory.save(session.model_copy(update={'usage': usage}), name + '_returned',
                                   result.model_dump(mode='json') if result is not None else {'result': None})
        if phase not in (Phase.EXECUTING, Phase.RECONCILING) and (
            usage.tokens > session.budget.tokens or usage.elapsed_seconds >= session.budget.elapsed_seconds
        ):
            self._stop(session, Phase.BUDGET, '调用后预算耗尽；没有执行网络操作')
        return session, result

    def advance(self, session_id: str, text: str | None = None) -> Session:
        session = self.memory.load(session_id)
        if text is not None:
            if session.operation_id and (session.result is None or session.result.restoration != 'restored'):
                raise ValueError('Existing operation is unresolved; query its result before changing requirements')
            message = Message(id='m' + str(len(session.messages) + 1), text=text)
            session = self._save(
                session, 'user_message', phase=Phase.READY,
                messages=(*session.messages, message), requirement_version=session.requirement_version + 1,
                requirement=None, observation=None, plan=None, operation_id=None, result=None, notes=(),
                read_facts=None, interpretation=None)
        try:
            if session.operation_id and session.phase in (
                Phase.EXECUTING, Phase.RECONCILING, Phase.UNKNOWN,
            ):
                return self._reconcile(session)
            if session.phase not in (Phase.READY, Phase.EXTRACTING, Phase.INSPECTING, Phase.PLANNING,
                                     Phase.REQUESTING_READ, Phase.INTERPRETING):
                return session
            return self._run(session)
        except StopRun as stopped:
            return stopped.session
        except CallFailed as failed:
            return self._save(failed.session, 'workflow_failed', phase=Phase.FAILED)

    def _extract(self, session: Session) -> Session:
        session, reply = self._call(
            session, Phase.EXTRACTING, 'model_extract', 'model_calls',
            lambda: ModelReply.model_validate(self.provider.extract(
                session.messages, session.budget.tokens - session.usage.tokens)))
        extraction: Extraction = reply.extraction
        requirement = extraction.requirement
        messages = {message.id: message.text for message in session.messages}
        if any(source.message_id not in messages or source.quote not in messages[source.message_id]
               for source in requirement.sources.values()):
            self._stop(session, Phase.FAILED, '需求字段的用户原话引用无效')
        session = self._save(session, 'requirement_extracted', requirement=requirement)
        issues = (*extraction.ambiguities, *requirement.conflicts(), *requirement.missing())
        if issues:
            self._stop(session, Phase.WAITING, '请补充或澄清：' + '、'.join(FIELD_LABELS.get(i, i) for i in issues))
        return session

    def _run(self, session: Session) -> Session:
        if session.mode != 'simulated':
            self._stop(session, Phase.FAILED, '会话模式不匹配；不会执行模拟实验')
        session = self._extract(session)
        requirement = session.requirement
        session, observation = self._call(
            session, Phase.INSPECTING, 'inspect_network', 'queries',
            lambda: Observation.model_validate(self.tools.inspect_network(self.now())))
        session = self._save(session, 'observation_bound', observation=observation)
        if not observation.observed_at <= self.now() < observation.expires_at:
            self._stop(session, Phase.REJECTED, '观测已过期或时间无效；本次不执行')
        session, preview = self._call(
            session, Phase.PLANNING, 'preview_policy', 'queries',
            lambda: Preview.model_validate(self.tools.preview_policy(
                requirement, session.requirement_version, observation, self.now())))
        if preview.status != 'planned':
            self._stop(session, Phase.REJECTED, preview.reason)
        self._validate_plan(session, preview.plan)
        session = self._save(session, 'plan_bound', plan=preview.plan)
        # Persist identity and deduplication claim atomically before entering the tool.
        operation_id = uuid4().hex
        try:
            session, result = self._call(
                session, Phase.EXECUTING, 'execute_experiment', 'experiments',
                lambda: OperationResult.model_validate(self.tools.execute_experiment(operation_id, session.plan)),
                operation_id=operation_id)
        except CallFailed as failed:
            return self._reconcile(failed.session)
        return self._finish(session, result)

    def _validate_plan(self, session: Session, plan: Plan):
        requirement, observation = session.requirement, session.observation
        binding = next((b for b in observation.bindings if b.user_id == requirement.user_id), None)
        expected = (
            BindService(user_id=requirement.user_id, service_id=requirement.service_id,
                        destination=requirement.destination),
            LimitBandwidth(user_id=requirement.user_id, service_id=requirement.service_id,
                           direction=requirement.direction, max_mbps=requirement.max_bandwidth_mbps),
        )
        valid = (
            plan.requirement_ref == fingerprint(requirement)
            and plan.requirement_version == session.requirement_version
            and plan.observation_ref == observation.ref
            and binding is not None and plan.session_id == binding.session_id
            and observation.observed_at <= self.now() < plan.expires_at <= observation.expires_at
            and plan.actions == expected
            and any(c.adapter_version == plan.adapter_version
                    and requirement.direction in c.directions
                    and requirement.destination in c.destinations
                    and requirement.max_bandwidth_mbps <= c.max_bandwidth_mbps
                    for c in observation.capabilities)
        )
        if not valid:
            self._stop(session, Phase.REJECTED, '方案与需求、现场、能力或有效期不一致；未执行')

    def _reconcile(self, session: Session) -> Session:
        try:
            session, result = self._call(
                session, Phase.RECONCILING, 'get_operation_result', 'queries',
                lambda: self._operation_result(session.operation_id))
        except CallFailed as failed:
            return self._save(failed.session, 'result_unknown', phase=Phase.UNKNOWN,
                              notes=('操作结果无法确认；不会重新执行，请继续查询或人工核对',))
        if result is None:
            return self._save(session, 'result_unknown', phase=Phase.UNKNOWN,
                              notes=('操作结果不明；保留操作标识，禁止重复执行',))
        return self._finish(session, result)

    def _operation_result(self, operation_id: str):
        result = self.tools.get_operation_result(operation_id)
        return OperationResult.model_validate(result) if result is not None else None

    def _finish(self, session: Session, result: OperationResult) -> Session:
        if result.operation_id != session.operation_id or result.plan_ref != session.plan.ref:
            return self._save(session, 'result_mismatch', phase=Phase.UNKNOWN,
                              notes=('返回结果不属于当前操作/方案；停止写入，保留原操作标识',))
        if result.restoration == 'failed':
            phase = Phase.RESTORE_FAILED
        elif result.outcome == 'unknown' or result.restoration == 'unknown':
            phase = Phase.UNKNOWN
        elif result.outcome == 'succeeded':
            phase = Phase.SUCCEEDED
        else:
            phase = Phase.FAILED
        return self._save(session, 'operation_finished', result=result, phase=phase,
                          notes=(result.detail,))
