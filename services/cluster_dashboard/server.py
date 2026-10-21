"""Loopback-only dashboard server and bounded, non-overlapping collector."""
import argparse
import datetime as dt
import json
import mimetypes
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .model import build
from .traffic import Traffic

ROOT = Path(__file__).resolve().parent


class Store:
    def __init__(self, source='ssh', host='free5gc-vm-1', interval=15, snapshot_file=None):
        self.source, self.host, self.interval = source, host, interval
        self.snapshot_file = snapshot_file
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.raw = None
        self.data = None
        self.error = None
        self.busy = False
        self.history = []
        self.last_attempt = None
        self.attempt_id = 0
        self.completed_attempt = 0
        self.started_at = dt.datetime.now(dt.timezone.utc).isoformat()
        self.traffic = Traffic(self)

    def collect(self):
        with self.lock:
            if self.busy:
                return
            self.busy = True
            self.attempt_id += 1
            self.last_attempt = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            if self.source == 'replay':
                raw = json.loads(Path(self.snapshot_file).read_text(encoding='utf-8-sig'))
            else:
                args = ['python3', str(ROOT / 'collect.py')]
                kwargs = {}
                if sys.platform == 'win32':
                    # A detached server has no console to inherit. Without this
                    # flag Windows can open a new console for every SSH poll.
                    kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
                if self.source == 'ssh':
                    args = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                            '-o', 'ServerAliveInterval=8', '-o', 'ServerAliveCountMax=2',
                            self.host, 'python3', '-']
                    kwargs['input'] = (ROOT / 'collect.py').read_text(encoding='utf-8')
                process = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                                         errors='replace', timeout=55, **kwargs)
                if process.returncode:
                    raise RuntimeError(process.stderr.strip()[-700:] or '采集进程退出')
                raw = json.loads(process.stdout)
            if raw.get('errors', {}).get('resources') or not raw.get('resources'):
                raise RuntimeError(raw.get('errors', {}).get('resources', '未收到集群资源'))
            data = build(raw, self.raw if self.source != 'replay' else None)
            with self.lock:
                self.raw, self.data, self.error = raw, data, None
                if not self.history or self.history[-1]['at'] != data['observedAt']:
                    # Only proxy upstream data; never sum all Pod interfaces as business throughput.
                    rates = [r['rxMbps'] for r in data['routes']]
                    tx_rates = [r['txMbps'] for r in data['routes']]
                    self.history.append({'at': data['observedAt'],
                                         'rxMbps': sum(rates) if rates and all(x is not None for x in rates) else None,
                                         'txMbps': sum(tx_rates) if tx_rates and all(x is not None for x in tx_rates) else None})
                    self.history = self.history[-120:]
        except Exception as exc:
            with self.lock:
                self.error = str(exc)[:800]
        finally:
            with self.lock:
                self.busy = False
                self.completed_attempt = self.attempt_id

    def request_refresh(self):
        with self.lock:
            target = self.attempt_id + 1
            self.wake.set()
            return target

    def loop(self):
        while not self.stop.is_set():
            self.wake.clear()
            self.collect()
            self.wake.wait(self.interval)

    def state(self):
        with self.lock:
            return {'source': self.source, 'host': self.host if self.source == 'ssh' else '本机' if self.source == 'local' else '归档回放',
                    'intervalSeconds': self.interval, 'busy': self.busy, 'error': self.error,
                    'lastAttempt': self.last_attempt, 'serverStartedAt': self.started_at,
                    'attemptId': self.attempt_id, 'completedAttempt': self.completed_attempt,
                    'data': self.data, 'history': list(self.history)}


def handler(store, port):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'CNFObservatory/1'

        def log_message(self, fmt, *args):
            if args and str(args[1] if len(args) > 1 else '') not in ('200', '202', '304'):
                super().log_message(fmt, *args)

        def permitted(self):
            # Prevent arbitrary websites from using local credentials via this server.
            host = self.headers.get('Host', '')
            if host not in (f'127.0.0.1:{port}', f'localhost:{port}'):
                return False
            origin = self.headers.get('Origin')
            return origin is None or origin in (f'http://127.0.0.1:{port}', f'http://localhost:{port}')

        def send(self, status, body, content_type='application/json; charset=utf-8', download=False):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            if download:
                self.send_header('Content-Disposition', 'attachment; filename="'+('traffic-report.json' if download=='traffic' else 'cluster-observation.json')+'"')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.permitted():
                return self.send(403, {'error': '仅允许本机同源访问'})
            path = urlsplit(self.path).path
            if path == '/api/traffic':
                return self.send(200, store.traffic.state())
            match = re.fullmatch(r'/api/traffic/([a-f0-9]{32})/report', path)
            if match:
                try:
                    return self.send(200, store.traffic.report(match[1]), download='traffic')
                except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
                    return self.send(503, {'error':str(exc)[:600]})
            if path in ('/api/state', '/api/export'):
                return self.send(200, store.state(), download=path == '/api/export')
            if path == '/healthz':
                return self.send(200, {'status': 'ok', 'meaning': '页面服务存活；集群状态请查询 /api/state'})
            allowed = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css', '/favicon.svg': 'favicon.svg', '/traffic.js':'traffic.js','/traffic.css':'traffic.css'}
            if path not in allowed:
                return self.send(404, {'error': '资源不存在'})
            file = ROOT / 'web' / allowed[path]
            content_type = mimetypes.guess_type(file)[0] or 'application/octet-stream'
            return self.send(200, file.read_bytes(), content_type + '; charset=utf-8')

        def do_POST(self):
            if not self.permitted():
                return self.send(403, {'error': '仅允许本机同源访问'})
            path = urlsplit(self.path).path
            if path.startswith('/api/traffic'):
                if path not in ('/api/traffic/start','/api/traffic/stop'):
                    return self.send(404, {'error':'操作不存在'})
                if self.headers.get('Sec-Fetch-Site') not in (None,'same-origin','none') or self.headers.get('X-Traffic-Token') != store.traffic.token:
                    return self.send(403, {'error':'请从本机观测台重新加载后操作。'})
                try:
                    if self.headers.get('Content-Type','').split(';')[0] != 'application/json' or self.headers.get('Transfer-Encoding'):
                        raise ValueError('此接口仅接受有限长度的 JSON 请求。')
                    length = int(self.headers.get('Content-Length','0'))
                    if not 1 <= length <= 8192:
                        raise ValueError('请求长度必须在 1–8192 字节内。')
                    self.connection.settimeout(5)
                    value = json.loads(self.rfile.read(length))
                    if path.endswith('/start'):
                        result = store.traffic.start(value)
                    else:
                        if not isinstance(value,dict) or set(value) != {'id'}:
                            raise ValueError('停止操作只接受任务 ID。')
                        result = store.traffic.stop(value['id'])
                    return self.send(202, result)
                except (ValueError, UnicodeDecodeError) as exc:
                    return self.send(400, {'error':str(exc)[:600]})
                except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
                    return self.send(409, {'error':str(exc)[:600]})
            if path != '/api/refresh':
                return self.send(404, {'error': '操作不存在'})
            if self.headers.get('Content-Length', '0') != '0' or self.headers.get('Transfer-Encoding'):
                return self.send(400, {'error': '此接口不接受请求参数'})
            target = store.request_refresh()
            return self.send(202, {'status': 'collecting', 'attempt': target,
                                   'meaning': '合并重复采集请求；仅执行固定只读检查'})
    return Handler


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='CNF 集群观测与受控 TCP 流量实验；默认通过 SSH 访问 VM1')
    parser.add_argument('--source', choices=['ssh', 'local', 'replay'], default='ssh')
    parser.add_argument('--ssh-host', default='free5gc-vm-1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--interval', type=int, default=15)
    parser.add_argument('--snapshot-file', help='仅 replay 模式，读取 collect.py 的归档输出')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,100}', args.ssh_host):
        parser.error('SSH 主机名格式不合法')
    if not 1024 <= args.port <= 65535 or not 10 <= args.interval <= 300:
        parser.error('port 必须在 1024..65535，interval 必须在 10..300 秒')
    if args.source == 'replay' and not args.snapshot_file:
        parser.error('replay 必须指定 --snapshot-file')
    store = Store(args.source, args.ssh_host, args.interval, args.snapshot_file)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler(store, args.port))
    thread = threading.Thread(target=store.loop, name='cluster-collector', daemon=True)
    thread.start()
    traffic_thread = threading.Thread(target=store.traffic.loop,name='traffic-jobs',daemon=True)
    traffic_thread.start()
    print(f'CNF 观测台 http://127.0.0.1:{args.port} · {args.source} · {args.interval}s · Ctrl+C 停止', flush=True)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        store.stop.set()
        store.traffic.shutdown.set()
        store.traffic.wake.set()
        store.wake.set()
        server.server_close()


if __name__ == '__main__':
    main()
