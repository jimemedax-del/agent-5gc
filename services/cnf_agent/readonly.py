"""F2 model/readonly orchestration. No execution adapter is reachable here."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request
from uuid import uuid4

from .schemas import Budget, InterpretationReply, Phase, ReadFacts, ReadRequest, Session
from .workflow import Workflow


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DashboardReader:
    """Return aggregate counts only; no IPs, subscriber IDs, secrets or writes."""
    url = 'http://127.0.0.1:8765/api/state'

    def __init__(self, now=None):
        self.now = now or (lambda: datetime.now(timezone.utc))

    def inspect_network(self, scope: str) -> ReadFacts:
        if scope != 'overview':
            raise ValueError('Only overview is permitted')
        try:
            opener = build_opener(ProxyHandler({}), NoRedirect())
            with opener.open(Request(self.url, method='GET'), timeout=8) as response:
                state = json.load(response)
            data = state.get('data')
            if state.get('error') or not data or state.get('source') not in ('ssh', 'local', 'replay'):
                raise ValueError('Unavailable source')
            at = datetime.fromisoformat(data['observedAt'].replace('Z', '+00:00'))
            age = (self.now() - at).total_seconds()
            if not 0 <= age <= 120:
                raise ValueError('Stale source')
            nodes, pods = data['nodes'], data['pods']
            return ReadFacts(mode='read_only_fixture' if state['source'] == 'replay' else 'read_only_observed',
                             observed_at=at.isoformat(), nodes_total=len(nodes),
                             nodes_ready=sum(n['ready'] is True for n in nodes), pods_total=len(pods),
                             pods_ready=sum(p['ready'] is True for p in pods), proxy_paths=len(data['routes']),
                             detail='聚合资源观测；Ready 和代理配置数量不证明注册、业务成功或 QoS；未执行配置或压测')
        except (OSError, ValueError, KeyError, TypeError):
            return ReadFacts(mode='unavailable', detail='当前观测不可用或已过期；不使用缓存推导现场正常')


class FixtureReader:
    def __init__(self, path: Path):
        self.facts = ReadFacts.model_validate_json(path.read_text(encoding='utf-8'))
        if self.facts.mode != 'read_only_fixture':
            raise ValueError('File input must be explicitly labelled read_only_fixture')

    def inspect_network(self, scope: str) -> ReadFacts:
        if scope != 'overview':
            raise ValueError('Only overview is permitted')
        return self.facts


def numeric_sources_valid(requirement) -> bool:
    """Check unit conversions against quoted values; not a general semantic judge."""
    for field in ('min_bandwidth_mbps', 'max_bandwidth_mbps', 'max_latency_ms', 'max_loss_percent'):
        value = getattr(requirement, field)
        if value is None:
            continue
        quote = requirement.sources[field].quote
        candidates = []
        if 'bandwidth' in field:
            for number, unit in re.findall(r'(\d+(?:\.\d+)?)\s*(Mbps|Gbps|Kbps|kbps|Mb/s|MB/s|兆比特/秒)', quote):
                scale = {'Gbps': 1000, 'Kbps': 0.001, 'kbps': 0.001, 'MB/s': 8}.get(unit, 1)
                candidates.append(float(number) * scale)
        elif field == 'max_latency_ms':
            candidates = [float(n) * (1000 if unit in ('s', '秒') else 1)
                          for n, unit in re.findall(r'(\d+(?:\.\d+)?)\s*(ms|毫秒|s|秒)', quote)]
        else:
            candidates = [float(n) for n in re.findall(r'(\d+(?:\.\d+)?)\s*[%％]', quote)]
        if not any(abs(candidate - value) < 1e-8 for candidate in candidates):
            return False
    return True


class ReadOnlyWorkflow(Workflow):
    def create(self, budget: Budget | None = None) -> Session:
        budget = budget or Budget(model_calls=10, queries=4, experiments=0, steps=16,
                                  tokens=65536, elapsed_seconds=240)
        if budget.experiments != 0:
            raise ValueError('F2 never grants experiment permission')
        return self.memory.create(Session(id=uuid4().hex, mode='real_model_readonly', budget=budget))

    def _run(self, session: Session) -> Session:
        if session.mode != 'real_model_readonly' or session.budget.experiments != 0:
            self._stop(session, Phase.FAILED, 'F2 会话权限不匹配；不会调用执行器')
        session = self._extract(session)
        if not numeric_sources_valid(session.requirement):
            self._stop(session, Phase.WAITING, '数值目标与引用原文或单位换算不一致，请明确目标')
        session, call = self._call(
            session, Phase.REQUESTING_READ, 'model_request_read', 'model_calls',
            lambda: ReadRequest.model_validate(self.provider.request_read(
                session.requirement, session.budget.tokens - session.usage.tokens)))
        session, facts = self._call(
            session, Phase.INSPECTING, 'inspect_network', 'queries',
            lambda: ReadFacts.model_validate(self.tools.inspect_network(call.scope)))
        session = self._save(session, 'readonly_facts', read_facts=facts)
        session, reply = self._call(
            session, Phase.INTERPRETING, 'model_explain_read', 'model_calls',
            lambda: InterpretationReply.model_validate(self.provider.explain(
                call, facts, session.budget.tokens - session.usage.tokens)))
        return self._save(session, 'readonly_complete', phase=Phase.READ_ONLY_COMPLETE,
                          interpretation=reply.interpretation,
                          notes=('真实模型只读分析已完成；没有配置下发或目标达标验收',))
