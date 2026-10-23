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
    parser = argparse.ArgumentParser(description='CNF Agent F1：仅离线模拟，无真实模型或集群调用')
    parser.add_argument('--db', type=Path, default=Path('.work/cnf-agent/f1.sqlite'))
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
    args = parser.parse_args(argv)
    try:
        if args.command == 'run' and not args.session and args.message is None:
            raise ValueError('A new session requires --message; omit it only to continue an existing session')
        if args.command == 'schema':
            from .schemas import Session
            import json
            print(json.dumps(Session.model_json_schema(), ensure_ascii=False, indent=2))
            return 0
        with Memory(args.db) as memory:
            if args.command in ('show', 'export'):
                session = memory.load(args.session)
                if args.command == 'export':
                    export_events(memory.events(session.id), args.output)
                print(summary(session))
                return 0
            replay = ReplayFile.model_validate_json(args.replay.read_text(encoding='utf-8'))
            if replay.version != 'cnf-agent-replay-v1' or replay.mode != 'simulated':
                raise ValueError('Only F1 simulated replay files are accepted')
            scenario = replay.scenarios[args.scenario]
            workflow = Workflow(memory, ReplayProvider(scenario.frames), SimulatedTools(scenario.fault))
            session = memory.load(args.session) if args.command == 'run' and args.session else workflow.create()
            messages = [frame.message for frame in scenario.frames] if args.command == 'demo' else [args.message]
            for message in messages:
                session = workflow.advance(session.id, message)
                print(summary(session))
                if session.phase != Phase.WAITING:
                    break
            if args.command == 'demo':
                return 0 if session.phase == scenario.expected_phase else 1
            return 0 if session.phase in (Phase.SUCCEEDED, Phase.WAITING) else 1
    except (ValueError, KeyError, OSError, ConcurrentUpdate) as error:
        print(f'F1 离线运行失败：{type(error).__name__}: {error}', file=sys.stderr)
        return 2
