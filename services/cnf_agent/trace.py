"""Human summaries and portable JSONL exports from canonical SQLite events."""
import json
from pathlib import Path

from .schemas import Session


LABELS = {
    'waiting_input': '等待补充需求', 'succeeded': '模拟任务通过', 'rejected': '请求被拒绝',
    'failed': '任务失败', 'result_unknown': '操作结果不明', 'restore_failed': '恢复失败',
    'budget_exceeded': '预算耗尽',
    'read_only_complete': '真实模型只读分析完成',
}


def summary(session: Session) -> str:
    mode = '模拟 F1：无真实模型/网络操作' if session.mode == 'simulated' else 'F2 真实模型，只读；未下发网络配置'
    lines = [f'[{mode}] 会话 {session.id}',
             f'状态：{LABELS.get(session.phase.value, session.phase.value)}；需求版本 {session.requirement_version}']
    lines.extend(session.notes)
    if session.mode == 'real_model_readonly' and session.requirement:
        lines.append('需求：' + json.dumps(session.requirement.model_dump(
            exclude={'version', 'sources'}, exclude_none=True), ensure_ascii=False))
    if session.read_facts:
        lines.append('观测来源：' + session.read_facts.mode)
    if session.interpretation:
        lines.append(session.interpretation.summary)
        lines.extend(session.interpretation.limitations)
    if session.plan:
        lines.append('模拟方案：' + ', '.join(action.kind for action in session.plan.actions))
    if session.result:
        lines.append(f'结果：{session.result.outcome}；恢复：{session.result.restoration}')
    lines.append('累计用量：' + session.usage.model_dump_json())
    return '\n'.join(lines)


def export_events(events: list[dict], path: str | Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        for event in events:
            stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n')
