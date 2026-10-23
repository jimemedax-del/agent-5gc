"""Offline CLI: scripted runs, continued conversations, inspection and export."""
import argparse
from pathlib import Path
import sys

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
    parser = argparse.ArgumentParser(description='CNF Agent：F1 离线回放，F2 真实模型只读分析')
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
    chat = commands.add_parser('chat', help='DeepSeek 真实模型；仅只读查询，无配置执行')
    chat.add_argument('--config', type=Path)
    chat.add_argument('--session')
    chat.add_argument('--message', required=True)
    chat.add_argument('--facts-file', type=Path, help='显式使用标注为夹具的只读数据，不冒充现场')
    args = parser.parse_args(argv)
    try:
        if args.command == 'run' and not args.session and args.message is None:
            raise ValueError('A new session requires --message; omit it only to continue an existing session')
        if args.command == 'schema':
            from .schemas import Session
            import json
            print(json.dumps(Session.model_json_schema(), ensure_ascii=False, indent=2))
            return 0
        db = args.db or Path('.work/cnf-agent/f2.sqlite' if args.command == 'chat' else '.work/cnf-agent/f1.sqlite')
        with Memory(db) as memory:
            if args.command in ('show', 'export'):
                session = memory.load(args.session)
                if args.command == 'export':
                    export_events(memory.events(session.id), args.output)
                print(summary(session))
                return 0
            if args.command == 'chat':
                return chat_readonly(args, memory)
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
