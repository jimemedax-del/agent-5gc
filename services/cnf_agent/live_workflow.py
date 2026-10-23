"""F3a: model extraction followed by deterministic E3 planning and one transaction."""
from datetime import datetime
from uuid import uuid4

from .live_contracts import LiveObservation, LivePreview, LiveSession, OperationSnapshot, PolicyReply
from .live_requirements import assess
from .live_tools import LiveTools, operation_result
from .schemas import Budget, Phase, fingerprint
from .workflow import CallFailed, Workflow


class LiveWorkflow(Workflow):
    def create(self, budget=None):
        budget = budget or Budget(model_calls=4, queries=32, experiments=1, steps=40,
                                  tokens=65536, elapsed_seconds=900)
        if budget.experiments != 1:
            raise ValueError('F3a permits exactly one transaction per session')
        return self.memory.create(LiveSession(id=uuid4().hex, budget=budget))

    def _run(self, session):
        if session.mode != 'real_model_live' or session.budget.experiments != 1:
            self._stop(session, Phase.FAILED, 'F3a 会话类型或实验权限不匹配')
        session, reply = self._call(
            session, Phase.EXTRACTING, 'model_extract_policy', 'model_calls',
            lambda: PolicyReply.model_validate(self.provider.extract_policy(
                session.messages, session.budget.tokens - session.usage.tokens)))
        requirement = reply.extraction
        session = self._save(session, 'policy_extracted', requirement=requirement)
        decision, notes = assess(requirement, session.messages)
        if decision != 'ready':
            self._stop(session, Phase.WAITING if decision == 'clarify' else Phase.REJECTED, *notes)
        session, observation = self._call(
            session, Phase.INSPECTING, 'inspect_network', 'queries',
            lambda: LiveObservation.model_validate(self.tools.inspect_network()))
        session = self._save(session, 'live_observation_bound', observation=observation)
        if not observation.observed_at <= self.now() < observation.expires_at:
            self._stop(session, Phase.REJECTED, '现场已过期；没有执行')
        session, preview = self._call(
            session, Phase.PLANNING, 'preview_policy', 'queries',
            lambda: LivePreview.model_validate(self.tools.preview_policy(
                requirement, session.requirement_version, observation)))
        if preview.status != 'planned':
            self._stop(session, Phase.WAITING if preview.status == 'needs_clarification' else Phase.REJECTED,
                       preview.detail)
        session = self._save(session, 'live_plan_bound', plan=preview.plan)
        self._validate_plan(session, preview.plan)
        operation_id = uuid4().hex
        try:
            session, snapshot = self._call(
                session, Phase.EXECUTING, 'execute_experiment', 'experiments',
                lambda: OperationSnapshot.model_validate(self.tools.execute_experiment(operation_id, session.plan, observation)),
                operation_id=operation_id)
        except CallFailed as failed:
            return self._reconcile(failed.session)
        return self._receive(session, snapshot)

    def _validate_plan(self, session, plan):
        observed = session.observation
        try:
            valid = (
                plan.mode == 'live' and plan.requirement_ref == fingerprint(session.requirement)
                and plan.requirement_version == session.requirement_version
                and plan.observation_ref == observed.ref and plan.release_id == observed.release_id
                and plan.batch == LiveTools.batch(session.requirement, observed, session.requirement_version)
                and plan.ref == plan.policy['policyPlanHash']
                and plan.policy['liveStateHash'] == observed.ref
                and plan.selected_mode == plan.policy['innerPlan']['mode']
                and plan.expires_at == datetime.fromisoformat(plan.policy['validUntil'])
                and observed.observed_at <= self.now() < plan.expires_at <= observed.expires_at
            )
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            self._stop(session, Phase.REJECTED, '方案与目标、现场或代码版本不一致；没有执行')

    def _reconcile(self, session):
        try:
            session, snapshot = self._call(
                session, Phase.RECONCILING, 'get_operation_result', 'queries',
                lambda: OperationSnapshot.model_validate(self.tools.get_operation_result(session.operation_id)))
        except CallFailed as failed:
            return self._save(failed.session, 'live_result_unknown', phase=Phase.UNKNOWN,
                              notes=('查询失败；保留原操作编号，不重新下发，请继续查询',))
        return self._receive(session, snapshot)

    def _receive(self, session, snapshot):
        if snapshot.operation_id != session.operation_id:
            return self._save(session, 'live_result_mismatch', phase=Phase.UNKNOWN,
                              notes=('返回了其他操作的结果；不执行或重放',))
        if snapshot.status != 'completed':
            phase = Phase.EXECUTING if snapshot.status == 'running' else Phase.UNKNOWN
            return self._save(session, 'live_operation_pending', phase=phase,
                              notes=('实网操作仍在执行；请用原会话查询' if phase == Phase.EXECUTING else
                                     '操作状态不明；不可重新下发，需继续查询或核对现场',))
        try:
            result = operation_result(snapshot, session.plan, session.observation)
            if result is None:
                raise ValueError('Completed operation has no result')
        except (ValueError, KeyError, TypeError):
            return self._save(session, 'live_result_invalid', phase=Phase.UNKNOWN,
                              notes=('结果证据与原方案不一致；没有确认成功或恢复',))
        return self._finish(session, result)
