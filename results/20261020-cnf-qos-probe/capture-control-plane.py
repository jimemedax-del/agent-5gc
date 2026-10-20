"""Persist only selected non-credential controller diagnostics for this probe."""
import argparse
from datetime import datetime, timezone
import json
import re
import os
import select
import subprocess
import time
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('folder', type=Path)
parser.add_argument('--label', choices=['initial', 'final'], required=True)
parser.add_argument('--follow-smf', action='store_true')
args = parser.parse_args()
meta = json.loads((args.folder / 'metadata.json').read_text())
if args.follow_smf:
    command = ['kubectl', '-n', 'free5gc', 'logs', 'deployment/free5gc-helm-free5gc-smf-smf',
               '--follow', '--tail=0']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 600
    buffer = b''
    count = 0
    try:
        with (args.folder / 'smf-control-follow.jsonl').open('x', encoding='utf-8') as stream:
            while time.monotonic() < deadline and not (args.folder / 'summary.json').exists():
                if not select.select([process.stdout], [], [], 1)[0]:
                    continue
                block = os.read(process.stdout.fileno(), 65536)
                if not block:
                    raise RuntimeError('SMF diagnostic stream ended unexpectedly')
                buffer += block
                while b'\n' in buffer:
                    raw, buffer = buffer.split(b'\n', 1)
                    line = raw.decode('utf-8', errors='replace')
                    if (re.search(r'QER|AMBR|QoS|QFI|Policy|policy|PFCP Session (Establishment|Deletion)', line) and
                            not re.search(r'token|authorization|password|secret|permanentkey|opc', line, re.I)):
                        stream.write(json.dumps({'capturedAt': datetime.now(timezone.utc).isoformat(), 'line': line}) + '\n')
                        stream.flush()
                        count += 1
    finally:
        process.terminate()
        process.wait(timeout=10)
        value = {'capturedAt': datetime.now(timezone.utc).isoformat(), 'command': command,
                 'selectedLines': count, 'probeSummaryObserved': (args.folder / 'summary.json').exists()}
        (args.folder / 'smf-control-follow-metadata.json').write_text(json.dumps(value, indent=2) + '\n')
    print(json.dumps(value), flush=True)
    raise SystemExit(0)
for nf in ['smf', 'pcf', 'upf']:
    command = ['kubectl', '-n', 'free5gc', 'logs', 'deployment/free5gc-helm-free5gc-' + nf + '-' + nf,
               '--since-time=' + meta['startedAt']]
    process = subprocess.run(command, text=True, capture_output=True, timeout=30, check=True)
    selected = [line for line in process.stdout.splitlines() if re.search(
        r'QER|AMBR|QoS|QFI|Policy|policy|PFCP Session (Establishment|Deletion)|sm-policies', line) and
        not re.search(r'token|authorization|password|secret|permanentkey|opc', line, re.I)]
    value = {'capturedAt': datetime.now(timezone.utc).isoformat(), 'command': command,
             'diagnosticSelectionOnly': True, 'lines': selected}
    with (args.folder / (nf + '-control-' + args.label + '.json')).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(nf, len(selected), 'selected lines', flush=True)
