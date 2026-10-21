"""Fixed VM1 job operations and durable worker; stdlib only, sent over SSH."""
import concurrent.futures
import datetime as dt
import fcntl
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

BASE = Path.home() / '.work' / 'cnf-dashboard-jobs'
TERMINAL = {'completed', 'stopped', 'failed', 'interrupted'}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def load(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def job_dir(job_id):
    if not isinstance(job_id, str) or not re.fullmatch('[a-f0-9]{32}', job_id):
        raise ValueError('Invalid job id')
    return BASE / job_id


def tick(pid):
    try:
        value = Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()
        return value[19] if value[0] != 'Z' else None
    except (OSError, ValueError):
        return None


def alive(directory):
    identity = load(directory / 'process.json', {})
    return bool(identity.get('tick') and tick(identity['pid']) == identity['tick'])


def kubectl(args, timeout=12):
    env = dict(os.environ, KUBECONFIG=str(Path.home() / '.kube' / 'config'))
    result = subprocess.run(['kubectl', *args], capture_output=True, text=True,
                            timeout=timeout, env=env)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-400:] or 'kubectl failed')
    return result.stdout


WRAPPER = r'''
marker="$1"; pidfile="$2"; cancel="$3"; shift 3
[ -e "$cancel" ] && exit 125
export CNF_DASHBOARD_FLOW="$marker"
printf '%s %s\n' "$$" "$(awk '{print $22}' /proc/$$/stat)" > "$pidfile"
[ -e "$cancel" ] && exit 125
exec "$@"
'''

# All paths and marker values below are generated from UUID and flow index.
# Persist cancellation before killing: even a delayed kubectl exec cannot start
# a new client after the cancellation latch has been installed in its UE.
KILL = r'''
marker="$1"; pidfile="$2"; cancel="$3"
touch "$cancel" || exit 2
[ -f "$pidfile" ] || { echo absent; exit 0; }
read pid started < "$pidfile"
case "$pid" in ''|*[!0-9]*) exit 3;; esac
matches() {
 [ -r "/proc/$pid/stat" ] || return 1
 [ "$(awk '{print $22}' /proc/$pid/stat)" = "$started" ] || return 1
 (tr '\000' '\n' < "/proc/$pid/environ" | grep -Fxq "CNF_DASHBOARD_FLOW=$marker") ||
 (tr '\000' '\n' < "/proc/$pid/cmdline" | grep -Fxq "$marker")
}
if matches; then
 kill -TERM "$pid" 2>/dev/null || true
 n=0
 while matches && [ "$n" -lt 15 ]; do sleep 0.1; n=$((n+1)); done
 matches && kill -KILL "$pid" 2>/dev/null
fi
matches && exit 4
echo cleared
'''


def identifiers(job_id, index):
    marker = job_id + '-' + str(index)
    return marker, '/tmp/cnf-dashboard-' + marker + '.pid', '/tmp/cnf-dashboard-' + job_id + '.cancel'


def cleanup(directory, info):
    errors = []
    for i, flow in enumerate(info.get('resolved', [])):
        try:
            current = json.loads(kubectl(['-n','free5gc','get','pod',flow['pod'],'--ignore-not-found','-o','json']))
            if not current or current.get('metadata',{}).get('uid') != flow['podUid']:
                # Do not interpret absence inside a different Pod as evidence
                # about the recorded container. Keep cleanup uncertain.
                raise RuntimeError('原 UE Pod 身份已消失或变化，客户端清理需要核对。')
            kubectl(['-n', 'free5gc', 'exec', flow['pod'], '--', 'sh', '-c', KILL,
                     'sh', *identifiers(info['id'], i)], timeout=10)
        except Exception as exc:
            errors.append(str(exc)[:300])
    return errors


def public(info):
    return {k:v for k,v in info.items() if k not in ('worker',)}


def reconcile(directory):
    info = load(directory / 'state.json')
    if info and info['status'] not in TERMINAL and not alive(directory):
        errors = cleanup(directory, info)
        info.update(status='unknown' if errors else 'interrupted', finishedAt=None if errors else now(),
                    message='任务进程中断；远端清理未确认，禁止启动新任务。' if errors else '任务进程中断，已确认本任务客户端退出。',
                    cleanupErrors=errors, updatedAt=now())
        write(directory / 'state.json', info)
    return info


def dispatch(request, source):
    BASE.mkdir(parents=True, exist_ok=True)
    with (BASE / 'manager.lock').open('a') as management:
        fcntl.flock(management, fcntl.LOCK_EX)
        action = request['action']
        if action == 'list':
            directories = sorted((d for d in BASE.iterdir() if d.is_dir() and re.fullmatch('[a-f0-9]{32}', d.name)),
                                 key=lambda d:d.stat().st_mtime, reverse=True)
            # Always inspect every active job, while bounding retained UI history.
            infos = [reconcile(d) for d in directories]
            active = [i for i in infos if i and i['status'] not in TERMINAL]
            history = sorted([i for i in infos if i and i['status'] in TERMINAL], key=lambda i:i['createdAt'], reverse=True)[:20]
            return {'jobs':[public(i) for i in active + history]}
        directory = job_dir(request['id'])
        if action == 'start':
            # Existing remote tasks survive local server/browser restarts.
            for other in BASE.iterdir():
                if other.is_dir() and re.fullmatch('[a-f0-9]{32}', other.name):
                    old = reconcile(other)
                    if old and old['status'] not in TERMINAL:
                        raise RuntimeError('已有任务正在运行或清理未确认，请先停止/恢复原任务。')
            lock_path = Path.home() / '.work' / 'cnf-planner-live.lock'
            with lock_path.open('a') as experiment:
                try:
                    fcntl.flock(experiment, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError('实验锁被其他任务占用，请等待其完成。')
            directory.mkdir(exist_ok=False)
            info = dict(id=request['id'], config=request['config'], status='starting',
                        createdAt=now(), updatedAt=now(), startedAt=None, finishedAt=None,
                        elapsedSeconds=0, progress=0, segment=0, completedSeconds=0,
                        results=[], resolved=[], message='正在检查真实 UE、服务端点与实验锁。')
            write(directory / 'state.json', info)
            (directory / 'worker.py').write_text(source, encoding='utf-8')
            with (directory / 'worker.log').open('ab') as log:
                process = subprocess.Popen([sys.executable, str(directory / 'worker.py'), 'worker', request['id']],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                    env=dict(os.environ, KUBECONFIG=str(Path.home()/'.kube'/'config')))
            write(directory / 'process.json', {'pid':process.pid, 'tick':tick(process.pid)})
            return {'job':public(info)}
        info = reconcile(directory)
        if not info:
            raise ValueError('任务不存在')
        if action == 'stop':
            (directory / 'cancel').touch()
            # The worker is the only writer while alive; avoid overwriting its
            # newest results from a stale stop request snapshot.
            return {'job':public(info), 'stopRequested':info['status'] not in TERMINAL}
        if action == 'get':
            return {'job':public(info)}
        raise ValueError('Unknown operation')


def resolve(info):
    pods = json.loads(kubectl(['get','pods','-A','-o','json']))['items']
    services = json.loads(kubectl(['get','services','-A','-o','json']))['items']
    slices = json.loads(kubectl(['get','endpointslices','-A','-o','json']))['items']
    nodes = json.loads(kubectl(['get','nodes','-o','json']))['items']
    output, targets = [], set()
    config = info['config']
    for flow in config['flows']:
        prefix = 'ueransim-ue-' if flow['ue']=='ue1' else 'cnf-a-ue2-'
        matches = [p for p in pods if p['metadata']['namespace']=='free5gc' and p['metadata']['name'].startswith(prefix)
                   and any(c['type']=='Ready' and c['status']=='True' for c in p['status'].get('conditions',[]))]
        if len(matches)!=1:
            raise RuntimeError(flow['ue']+' 没有唯一 Ready Pod')
        pod = matches[0]
        addresses = json.loads(kubectl(['-n','free5gc','exec',pod['metadata']['name'],'--','ip','-j','-4','addr','show','uesimtun0']))
        ips = [a['local'] for i in addresses for a in i.get('addr_info',[]) if a.get('family')=='inet']
        if len(ips)!=1:
            raise RuntimeError(flow['ue']+' 隧道 IP 不明确')
        if config['environment']=='cnf-lab':
            service_name = 'cnf-m1-backend' if flow['target']=='core' else 'cnf-m2-edge'
            listen = 15201 if flow['target']=='core' else 15202
        else:
            service_name = 'cnf-a-'+flow['target']+'-'+flow['ue']
            listen = 15301 + (flow['ue']=='ue2') + (2 if flow['target']=='edge' else 0)
        svc = next((s for s in services if s['metadata']['namespace']==config['environment'] and s['metadata']['name']==service_name), None)
        if not svc:
            raise RuntimeError('业务 Service 不存在')
        endpoints = [e for sl in slices if sl['metadata']['namespace']==config['environment'] and
            sl['metadata'].get('labels',{}).get('kubernetes.io/service-name')==service_name
            for e in sl.get('endpoints',[]) if e.get('conditions',{}).get('ready') is True]
        if len(endpoints)!=1 or len(endpoints[0].get('addresses',[]))!=1:
            raise RuntimeError('业务后端没有唯一 Ready 端点')
        identity = endpoints[0]['addresses'][0]+':5201'
        if identity in targets:
            raise RuntimeError('多条流指向同一个 iperf3 后端，请选择独立后端。')
        targets.add(identity)
        gateways = [s for s in services if s['metadata']['namespace']==config['environment']
                    and any(p.get('port')==listen and p.get('nodePort') for p in s['spec'].get('ports',[]))]
        if len(gateways)!=1:
            raise RuntimeError('代理 NodePort 映射不明确')
        port = next(p['nodePort'] for p in gateways[0]['spec']['ports'] if p['port']==listen)
        expected = listen + 16000
        if port != expected:
            raise RuntimeError('NodePort 与受控端口映射不一致')
        proxies = [p for p in pods if p['metadata']['namespace']==config['environment'] and 'envoy' in p['metadata']['name'] and
                   any(c['type']=='Ready' and c['status']=='True' for c in p['status'].get('conditions',[]))]
        if len(proxies)!=1:
            raise RuntimeError('没有唯一 Ready 代理 Pod')
        node = next(n for n in nodes if n['metadata']['name']==proxies[0]['spec']['nodeName'])
        host = next(a['address'] for a in node['status']['addresses'] if a['type']=='InternalIP')
        output.append(dict(flow, pod=pod['metadata']['name'], podUid=pod['metadata']['uid'], ip=ips[0], host=host,
                           port=port, listener=listen, service=service_name, backendIP=endpoints[0]['addresses'][0]))
    return output


def valid_result(result, flow, seconds):
    try:
        start = result['start']
        connected = start['connected']
        test = start['test_start']
        if not connected or not all(c['local_host']==flow['ip'] and c['remote_port']==flow['port'] for c in connected):
            return False
        if test['reverse'] != int(flow['direction']=='dl') or test['duration'] != seconds or test['protocol']!='TCP':
            return False
        for key in ('sum_received','sum_sent'):
            summary = result['end'][key]
            for field in ('bytes','seconds','bits_per_second'):
                value = summary[field]
                if type(value) not in (int,float) or value < 0 or not math.isfinite(value):
                    return False
            if summary['seconds'] <= 0 or summary['bytes'] <= 0 or summary['bits_per_second'] <= 0:
                return False
        return True
    except (KeyError,TypeError,ValueError,OverflowError):
        return False


def worker(job_id):
    directory = job_dir(job_id)
    info = load(directory / 'state.json')
    clients = []
    cancelled = False
    def update(**fields):
        info.update(fields, updatedAt=now())
        write(directory / 'state.json', info)
    def cancel_requested():
        return (directory/'cancel').exists()
    experiment = (Path.home()/'.work'/'cnf-planner-live.lock').open('a')
    held = False
    try:
        try:
            fcntl.flock(experiment, fcntl.LOCK_EX|fcntl.LOCK_NB)
            held = True
        except BlockingIOError:
            raise RuntimeError('实验锁被其他任务占用，本次未发流。')
        update(resolved=resolve(info))
        # Record all targets before launching a single client, for crash cleanup.
        if cancel_requested():
            update(status='stopped', finishedAt=now(), message='已取消，未启动流量。')
            return
        started = time.monotonic()
        update(status='running', startedAt=now(), message='正在发送 TCP 流量。')
        duration = info['config']['durationSeconds']
        done = 0
        while done < duration:
            seconds = min(45, duration-done)
            segment = info['segment']+1
            update(segment=segment, segmentSeconds=seconds)
            clients = []
            files = []
            for i, flow in enumerate(info['resolved']):
                stdout = (directory/('segment-%d-flow-%d.json'%(segment,i))).open('w')
                stderr = (directory/('segment-%d-flow-%d.err'%(segment,i))).open('w')
                files += [stdout,stderr]
                iperf = ['iperf3','-c',flow['host'],'-p',str(flow['port']),'-B',flow['ip'],
                         '-b',str(flow['rateMbps'])+'M','-t',str(seconds),'-l','16384','-P','1','-J','--connect-timeout','5000']
                if flow['direction']=='dl':
                    iperf.append('-R')
                proc = subprocess.Popen(['kubectl','-n','free5gc','exec',flow['pod'],'--',
                    'sh','-c',WRAPPER,'sh',*identifiers(job_id,i),*iperf],stdout=stdout,stderr=stderr)
                clients.append(proc)
            segment_start = time.monotonic()
            try:
                while any(p.poll() is None for p in clients):
                    if cancel_requested() or time.monotonic()-segment_start>seconds+15:
                        cancelled = cancel_requested()
                        update(status='stopping', message='正在确认 UE 内的客户端退出。')
                        errors = cleanup(directory, info)
                        if errors:
                            raise RuntimeError('客户端清理未确认：'+'; '.join(errors))
                        for proc in clients:
                            try: proc.wait(timeout=5)
                            except subprocess.TimeoutExpired: proc.terminate()
                        break
                    elapsed = time.monotonic()-started
                    update(elapsedSeconds=round(elapsed,1), progress=min(99,int((done+min(seconds,time.monotonic()-segment_start))/duration*100)))
                    time.sleep(.5)
            finally:
                for f in files:
                    f.close()
            failed = False
            for i, flow in enumerate(info['resolved']):
                raw = (directory/('segment-%d-flow-%d.json'%(segment,i))).read_text()
                error = (directory/('segment-%d-flow-%d.err'%(segment,i))).read_text()[-500:]
                try: result = json.loads(raw)
                except ValueError: result = {'error':error or '客户端未返回完整结果'}
                ok = clients[i].poll()==0 and 'error' not in result
                valid = valid_result(result,flow,seconds)
                ok = ok and valid
                receiver = result.get('end',{}).get('sum_received',{})
                sender = result.get('end',{}).get('sum_sent',{})
                info['results'].append({'flowIndex':i,'segment':segment,'status':'completed' if ok else 'stopped' if cancelled else 'failed',
                    'receiverMbps':receiver.get('bits_per_second',0)/1e6 if ok else None,
                    'senderMbps':sender.get('bits_per_second',0)/1e6 if ok else None,
                    'receivedBytes':receiver.get('bytes') if ok else None,
                    'seconds':receiver.get('seconds') if ok else None,
                    'error':None if ok else str(result.get('error') or error or '客户端/隧道绑定检查未通过')[:500]})
                failed |= not ok
            if cancelled:
                update(status='stopped', finishedAt=now(), message='已确认本任务 UE 客户端退出；部分分段没有完整吞吐结果。')
                return
            if failed:
                raise RuntimeError('部分流量失败，已保留分段结果。')
            done += seconds
            update(completedSeconds=done, message='当前分段完成，准备重连。' if done<duration else '全部分段完成。')
            if cancel_requested():
                cancelled = True
                break
        errors = cleanup(directory,info)
        if errors:
            raise RuntimeError('收尾确认失败：'+'; '.join(errors))
        update(status='stopped' if cancelled else 'completed', finishedAt=now(),
               progress=info['progress'] if cancelled else 100,
               elapsedSeconds=round(time.monotonic()-started,1),
               message='任务已停止。' if cancelled else '所有分段均成功，客户端已退出。')
    except Exception as exc:
        errors = cleanup(directory, info)
        for proc in clients:
            if proc.poll() is None:
                proc.terminate()
        update(status='unknown' if errors else 'failed', finishedAt=None if errors else now(),
               cleanupErrors=errors, message=str(exc)[:600])
        while errors and held:
            # Keep the experiment lock until actual UE cleanup is confirmed.
            time.sleep(3)
            errors = cleanup(directory, info)
            update(status='unknown' if errors else 'failed', cleanupErrors=errors,
                   finishedAt=None if errors else now(),
                   message='客户端清理未确认，持有实验锁重试。' if errors else '异常任务已清理，客户端已退出。')
    finally:
        experiment.close()


if __name__=='__main__' and len(sys.argv)==3 and sys.argv[1]=='worker':
    worker(sys.argv[2])
