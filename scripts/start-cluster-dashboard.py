#!/usr/bin/env python3
"""Start the default dashboard independently of the invoking terminal/session."""
import datetime as dt
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:8765'
LOGS = ROOT / '.work' / 'cluster-dashboard' / 'runtime'


def running():
    try:
        with urlopen(URL + '/healthz', timeout=1) as response:
            return (response.headers.get('Server', '').startswith('CNFObservatory/')
                    and json.load(response).get('status') == 'ok')
    except (OSError, URLError, ValueError):
        return False


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if running():
        print('观测台已经运行，无需重复启动：' + URL)
        return 0
    with socket.socket() as sock:
        sock.settimeout(1)
        if sock.connect_ex(('127.0.0.1', 8765)) == 0:
            print('8765 端口被其他服务占用；未启动或终止任何进程。')
            return 1
    LOGS.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-u', str(ROOT / 'scripts' / 'cluster-dashboard.py')]
    options = {'cwd': str(ROOT), 'stdin': subprocess.DEVNULL, 'close_fds': True}
    if os.name == 'nt':
        # Break away from the temporary execution job, detach from its console,
        # and create no visible terminal. Fail explicitly if Windows forbids it.
        options['creationflags'] = (subprocess.DETACHED_PROCESS |
                                    subprocess.CREATE_NEW_PROCESS_GROUP |
                                    subprocess.CREATE_BREAKAWAY_FROM_JOB)
    else:
        options['start_new_session'] = True
    with (LOGS / 'stdout.log').open('ab', buffering=0) as stdout, (LOGS / 'stderr.log').open('ab', buffering=0) as stderr:
        try:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr, **options)
        except OSError as exc:
            print('独立后台启动失败：' + str(exc))
            print('可在自己的终端运行 python scripts/cluster-dashboard.py，并保持终端打开。')
            return 1
    metadata = {'pid': process.pid, 'startedAt': dt.datetime.now(dt.timezone.utc).isoformat(),
                'command': command, 'url': URL, 'mode': 'independent-background'}
    (LOGS / 'process.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    for _ in range(40):
        if running():
            print('观测台已独立后台启动：' + URL)
            print('PID: ' + str(process.pid))
            print('日志：' + str(LOGS))
            return 0
        if process.poll() is not None:
            print('启动进程已退出，退出码：' + str(process.returncode))
            print('请查看：' + str(LOGS / 'stderr.log'))
            return 1
        time.sleep(.2)
    print('后台进程仍在，但页面尚未就绪，请查看日志：' + str(LOGS))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
