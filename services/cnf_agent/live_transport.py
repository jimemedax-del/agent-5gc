"""Operator-owned SSH transport and content-addressed E3 code snapshots."""
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = r'''
import base64, hashlib, io, json, os, pathlib, zipfile
data = json.load(__import__('sys').stdin)
raw = base64.b64decode(data['archive'], validate=True)
digest = hashlib.sha256(raw).hexdigest()
if data['release_id'] != digest:
    raise ValueError('Bundle hash mismatch')
root = pathlib.Path.home() / '.work/cnf-agent-f3a/releases' / digest
root.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(io.BytesIO(raw)) as archive:
    for item in archive.infolist():
        relative = pathlib.PurePosixPath(item.filename)
        if relative.is_absolute() or '..' in relative.parts or item.is_dir():
            raise ValueError('Invalid bundle path')
        target = root.joinpath(*relative.parts)
        content = archive.read(item)
        if target.exists():
            if not target.is_file() or target.read_bytes() != content:
                raise ValueError('Existing immutable release differs')
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(content)
print(json.dumps({'ok': True, 'value': {'release_id': digest}}))
'''


def bundle(root=ROOT):
    files = [path for directory in ('scripts', 'infra', 'tests')
             for path in (root / directory).rglob('*') if path.is_file() and '__pycache__' not in path.parts]
    files += [root / 'services/cnf_agent/live_worker.py']
    files += [root / 'results' / name / 'metadata.json' for name in
              ('20261020-cnf-contention', '20261020-cnf-contention-uplink')]
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            entry = zipfile.ZipInfo(path.relative_to(root).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, path.read_bytes())
    raw = stream.getvalue()
    return hashlib.sha256(raw).hexdigest(), raw


class TransportError(RuntimeError):
    pass


class SshTransport:
    def __init__(self, release_id: str | None = None):
        if release_id is not None and not re.fullmatch('[a-f0-9]{64}', release_id):
            raise ValueError('Invalid release identifier')
        self.release_id = release_id

    def _python(self, source, payload, timeout):
        # Only trusted Python source forms the remote command. Model data travels as JSON stdin.
        command = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                   '-o', 'ServerAliveInterval=8', '-o', 'ServerAliveCountMax=2',
                   'free5gc-vm-1', 'python3 -c ' + shlex.quote(source)]
        kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
        try:
            run = subprocess.run(command, input=json.dumps(payload), capture_output=True, text=True,
                                 encoding='utf-8', timeout=timeout, **kwargs)
            if run.returncode:
                raise TransportError('VM1 transport failed; no command replay')
            value = json.loads(run.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise TransportError('VM1 response unavailable; query the existing operation before any retry') from None
        if value.get('ok') is not True:
            raise TransportError('VM1 tool rejected: ' + str(value.get('code', 'unknown')))
        return value['value']

    def deploy(self):
        release_id, archive = bundle()
        value = self._python(INSTALLER, {'release_id': release_id,
                            'archive': base64.b64encode(archive).decode('ascii')}, timeout=90)
        if value['release_id'] != release_id:
            raise TransportError('Unexpected deployed release')
        self.release_id = release_id
        return release_id

    def call(self, command, arguments):
        if self.release_id is None:
            raise ValueError('Deploy the fixed release before calling VM1')
        if command not in ('inspect', 'preview', 'execute', 'result'):
            raise ValueError('Unsupported live tool')
        source = ("import pathlib, runpy; path=pathlib.Path.home()/'.work/cnf-agent-f3a/releases'/"
                  + repr(self.release_id) + "/'services/cnf_agent/live_worker.py'; runpy.run_path(str(path),run_name='__main__')")
        return self._python(source, {'command': command, 'arguments': arguments},
                            timeout=150 if command == 'inspect' else 30)
