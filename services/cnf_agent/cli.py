"""CLI for replay, readonly model analysis and controlled live transactions."""
import argparse
from pathlib import Path
import sys
import time

from pydantic import Field

from .memory import ConcurrentUpdate, Memory
from .provider import ReplayFrame, ReplayProvider
from .schemas import Contract, Phase
from .tools import Fault, SimulatedTools
from .trace import export_events, summary
from .workflow import Workflow


class Scenario(Contract):
    frames: tuple[ReplayFrame, ...] = Field(min_length=1)
    fault: Fault = 'none'
    expected_phase: Phase


class ReplayFile(Contract):
    version: str
    mode: str
    scenarios: dict[str, Scenario]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='CNF Agent：F1 回放、F2 只读分析、F3a 受控实网实验')
    parser.add_argument('--db', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('demo', 'run'):
        command = commands.add_parser(name)
        command.add_argument('--replay', type=Path, required=True)
        command.add_argument('--scenario', required=True)
        if name == 'run':
            command.add_argument('--session')
            command.add_argument('--message')
    for name in ('show', 'export', 'schema'):
        command = commands.add_parser(name)
        if name != 'schema':
            command.add_argument('session')
        if name == 'export':
            command.add_argument('--output', type=Path, required=True)
        if name == 'schema':
            command.add_argument('--scope', choices=('replay', 'live'), default='replay')
    chat = commands.add_parser('chat', help='DeepSeek 真实模型；仅只读查询，无配置执行')
    chat.add_argument('--config', type=Path)
    chat.add_argument('--session')
    chat.add_argument('--message', required=True)
    chat.add_argument('--facts-file', type=Path, help='显式使用标注为夹具的只读数据，不冒充现场')
    live = commands.add_parser('live', help='F3a：真实模型与一次 E3 受控下行实验；会写入并恢复专用队列')
    live.add_argument('--config', type=Path)
    live.add_argument('--session')
    live.add_argument('--message')
    live.add_argument('--wait', action='store_true', help='等待已有操作结果，最多 180 秒；超时不重放')
    args = parser.parse_args(argv)
    try:
        if args.command == 'run' and not args.session and args.message is None:
            raise ValueError('A new session requires --message; omit it only to continue an existing session')
        if args.command == 'schema':
            from .schemas import Session
            import json
            if args.scope == 'live':
                from .live_contracts import LiveSession
                print(json.dumps(LiveSession.model_json_schema(), ensure_ascii=False, indent=2))
            else:
                print(json.dumps(Session.model_json_schema(), ensure_ascii=False, indent=2))
            return 0
        default_db = {'chat': 'f2.sqlite', 'live': 'f3a.sqlite'}.get(args.command, 'f1.sqlite')
        db = args.db or Path('.work/cnf-agent') / default_db
        with Memory(db) as memory:
            if args.command in ('show', 'export'):
                session = memory.load(args.session)
                if args.command == 'export':
                    export_events(memory.events(session.id), args.output)
                print(summary(session))
                return 0
            if args.command == 'chat':
                return chat_readonly(args, memory)
            if args.command == 'live':
                return live_experiment(args, memory)
            replay = ReplayFile.model_validate_json(args.replay.read_text(encoding='utf-8'))
            if replay.version != 'cnf-agent-replay-v1' or replay.mode != 'simulated':
                raise ValueError('Only F1 simulated replay files are accepted')
            scenario = replay.scenarios[args.scenario]
            workflow = Workflow(memory, ReplayProvider(scenario.frames), SimulatedTools(scenario.fault))
            session = memory.load(args.session) if args.command == 'run' and args.session else workflow.create()
            if session.mode != 'simulated':
                raise ValueError('F1 replay cannot continue an F2 session')
            messages = [frame.message for frame in scenario.frames] if args.command == 'demo' else [args.message]
            for message in messages:
                session = workflow.advance(session.id, message)
                print(summary(session))
                if session.phase != Phase.WAITING:
                    break
            if args.command == 'demo':
                return 0 if session.phase == scenario.expected_phase else 1
            return 0 if session.phase in (Phase.SUCCEEDED, Phase.WAITING) else 1
    except (ValueError, KeyError, OSError, ImportError, ConcurrentUpdate) as error:
        print(f'Agent 运行失败：{type(error).__name__}: {error}', file=sys.stderr)
        return 2


def chat_readonly(args, memory):
    from .deepseek import DeepSeekProvider, DeepSeekSettings
    from .readonly import DashboardReader, FixtureReader, ReadOnlyWorkflow

    default_config = Path('.work/cnf-agent/deepseek.local.json')
    path = args.config if args.config is not None else (default_config if default_config.exists() else None)
    settings = DeepSeekSettings.load(path)
    provider = DeepSeekProvider(settings)
    try:
        reader = FixtureReader(args.facts_file) if args.facts_file else DashboardReader()
        workflow = ReadOnlyWorkflow(memory, provider, reader)
        session = memory.load(args.session) if args.session else workflow.create()
        if session.mode != 'real_model_readonly':
            raise ValueError('F2 cannot continue a replay session')
        session = workflow.advance(session.id, args.message)
        print(summary(session))
        return 0 if session.phase in (Phase.READ_ONLY_COMPLETE, Phase.WAITING) else 1
    finally:
        provider.close()


def live_experiment(args, memory):
    from .deepseek import DeepSeekProvider, DeepSeekSettings
    from .live_tools import LiveTools
    from .live_transport import SshTransport
    from .live_workflow import LiveWorkflow

    if args.session is None and args.message is None:
        raise ValueError('A new live session requires --message')
    session = memory.load(args.session) if args.session else None
    if session is not None and session.mode != 'real_model_live':
        raise ValueError('F3a cannot continue a replay/readonly session')
    release_id = session.observation.release_id if session and session.observation else None
    transport = SshTransport(release_id)
    provider = None
    if args.message is not None:
        default = Path('.work/cnf-agent/deepseek.local.json')
        path = args.config if args.config is not None else (default if default.exists() else None)
        provider = DeepSeekProvider(DeepSeekSettings.load(path))
    try:
        workflow = LiveWorkflow(memory, provider, LiveTools(transport))
        session = session or workflow.create()
        session = workflow.advance(session.id, args.message)
        deadline = time.monotonic() + 180
        while (args.wait and session.result is None and session.phase in (Phase.EXECUTING, Phase.UNKNOWN)
               and time.monotonic() < deadline):
            time.sleep(3)
            session = workflow.advance(session.id)
        print(summary(session))
        return 0 if session.phase in (Phase.SUCCEEDED, Phase.WAITING, Phase.EXECUTING) else 1
    finally:
        if provider is not None:
            provider.close()
