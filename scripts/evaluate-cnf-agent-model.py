#!/usr/bin/env python3
"""Run frozen F2 development cases with a real model and explicit readonly fixture."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.cnf_agent.deepseek import DeepSeekProvider, DeepSeekSettings
from services.cnf_agent.memory import Memory
from services.cnf_agent.readonly import FixtureReader, ReadOnlyWorkflow
from services.cnf_agent.trace import export_events


def run_case(case, settings, facts_file, output):
    provider = DeepSeekProvider(settings)
    try:
        with Memory(output / (case['id'] + '.sqlite')) as memory:
            workflow = ReadOnlyWorkflow(memory, provider, FixtureReader(facts_file))
            session = workflow.create()
            for message in case['messages']:
                session = workflow.advance(session.id, message)
            actual = session.requirement.model_dump() if session.requirement else {}
            events = memory.events(session.id)
            extractions = [event['payload']['extraction'] for event in events
                           if event['kind'] == 'model_extract_returned']
            mismatches = {key: {'expected': value, 'actual': actual.get(key)}
                          for key, value in case['expected'].items() if actual.get(key) != value}
            state_match = session.phase.value == case['state']
            ambiguity_match = not case.get('ambiguity_required') or bool(
                extractions and extractions[-1]['ambiguities'])
            result = dict(id=case['id'], state=session.phase.value, expected_state=case['state'],
                          mismatches=mismatches, state_match=state_match, ambiguity_match=ambiguity_match,
                          passed=not mismatches and state_match and ambiguity_match,
                          usage=session.usage.model_dump(), session_id=session.id)
            (output / (case['id'] + '.json')).write_text(session.model_dump_json(indent=2), encoding='utf-8')
            export_events(events, output / (case['id'] + '.jsonl'))
            print(f"{case['id']}: {session.phase.value}, matched={result['passed']}", flush=True)
            return result
    finally:
        provider.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--freeze', type=Path, required=True)
    parser.add_argument('--facts-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, choices=(1, 2), default=1)
    args = parser.parse_args(argv)
    frozen = json.loads(args.freeze.read_text(encoding='utf-8'))
    digest = hashlib.sha256(args.cases.read_bytes()).hexdigest()
    if frozen['sha256'] != digest:
        raise ValueError('Cases changed after freezing; no model calls were made')
    cases = json.loads(args.cases.read_text(encoding='utf-8'))
    names = [case['id'] for case in cases['cases']]
    if len(names) != len(set(names)) or not all(re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name) for name in names):
        raise ValueError('Case identifiers must be unique safe filenames')
    settings = DeepSeekSettings.load(args.config)
    FixtureReader(args.facts_file)
    args.output.mkdir(parents=True, exist_ok=False)
    start = dict(mode='real_model_readonly', model=settings.model, base_url=settings.base_url,
                 case_sha256=digest, labels=cases['label_source'], stage='R2.S5.F2',
                 scope='Development acceptance with readonly fixture; not independent accuracy or network validation')
    (args.output / 'metadata.json').write_text(json.dumps(start, indent=2), encoding='utf-8')
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        runs = list(executor.map(lambda case: run_case(case, settings, args.facts_file, args.output), cases['cases']))
    result = dict(**start, passed=sum(run['passed'] for run in runs), total=len(runs), runs=runs,
                  tokens=sum(run['usage']['tokens'] for run in runs),
                  model_calls=sum(run['usage']['model_calls'] for run in runs), network_writes=0)
    (args.output / 'acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"{result['passed']}/{result['total']} development cases matched; tokens={result['tokens']}")
    return 0 if result['passed'] == result['total'] else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
