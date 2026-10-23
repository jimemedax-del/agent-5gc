"""VM1-only fixed RPC and detached E3 jobs. Uses only the standard library."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
WORK = Path.home() / '.work/cnf-agent-f3a'
OPERATIONS = WORK / 'operations'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write(path, value):
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def policy_module():
    os.environ.setdefault('KUBECONFIG', str(Path.home() / '.kube/config'))
    spec = importlib.util.spec_from_file_location('agent_e3', ROOT / 'scripts/cnf-planner-policy.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def operation_path(operation_id):
    if not isinstance(operation_id, str) or not re.fullmatch('[a-f0-9]{32}', operation_id):
        raise ValueError('Invalid operation identifier')
    return OPERATIONS / operation_id


def process_stamp(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except (OSError, IndexError):
        return None


def query(operation_id):
    folder = operation_path(operation_id)
    result = folder / 'result.json'
    if result.is_file():
        return {'operation_id': operation_id, 'status': 'completed', 'result': read(result), 'evidence': str(folder)}
    launch = folder / 'launch.json'
    running = False
    if launch.is_file():
        process = read(launch)
        running = process['stamp'] is not None and process_stamp(process['pid']) == process['stamp']
    return {'operation_id': operation_id, 'status': 'running' if running else 'unknown',
            'result': None, 'evidence': str(folder)}


def start(operation_id, payload):
    folder = operation_path(operation_id)
    OPERATIONS.mkdir(parents=True, exist_ok=True)
    try:
        folder.mkdir()
    except FileExistsError:
        receipt = folder / 'receipt.json'
        if not receipt.is_file() or read(receipt)['payload_hash'] != digest(payload):
            raise ValueError('Operation already claimed or payload differs; never restart')
        return query(operation_id)
    write(folder / 'receipt.json', {'operation_id': operation_id, 'payload_hash': digest(payload), 'payload': payload})
    with (folder / 'worker.log').open('ab') as output:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--run', operation_id],
                                   stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True)
    write(folder / 'launch.json', {'pid': process.pid, 'stamp': process_stamp(process.pid)})
    return query(operation_id)


def run_operation(operation_id):
    folder = operation_path(operation_id)
    receipt = read(folder / 'receipt.json')
    # The unique run marker also prevents direct accidental invocation twice.
    with (folder / 'started').open('x'):
        pass
    payload = receipt['payload']
    policy = policy_module()
    error_code = None
    try:
        policy.run(folder / 'e3', payload['batch'], payload['state'], payload['plan'])
    except Exception as error:
        error_code = getattr(error, 'code', type(error).__name__)
    summary_file = folder / 'e3/summary.json'
    verification = folder / 'e3/verification.json'
    write(folder / 'result.json', {
        'payload_hash': receipt['payload_hash'], 'plan_ref': payload['plan']['policyPlanHash'],
        'summary': read(summary_file) if summary_file.is_file() else {'outcome': 'unknown'},
        'verification': read(verification) if verification.is_file() else None, 'error_code': error_code})


def rpc(request):
    command, arguments = request['command'], request['arguments']
    if command == 'result' and set(arguments) == {'operation_id'}:
        return query(arguments['operation_id'])
    if command == 'execute' and set(arguments) == {'operation_id', 'state', 'batch', 'plan'}:
        operation_id = arguments['operation_id']
        return start(operation_id, {key: arguments[key] for key in ('state', 'batch', 'plan')})
    policy = policy_module()
    if command == 'inspect' and not arguments:
        folder = WORK / 'inspections' / uuid4().hex
        folder.mkdir(parents=True)
        with policy.Backend() as backend:
            state = policy.inspect_state(backend, folder / 'live')['state']
        write(folder / 'state.json', state)
        return state
    if command == 'preview' and set(arguments) == {'state', 'batch'}:
        return policy.plan_policy(arguments['state'], arguments['batch'])
    raise ValueError('Unsupported fixed RPC command/arguments')


def main():
    if sys.platform != 'linux':
        raise ValueError('Run the worker only on VM1')
    if len(sys.argv) == 3 and sys.argv[1] == '--run':
        run_operation(sys.argv[2])
        return
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            value = rpc(json.load(sys.stdin))
        result = {'ok': True, 'value': value}
    except Exception as error:
        result = {'ok': False, 'code': getattr(error, 'code', type(error).__name__)}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
