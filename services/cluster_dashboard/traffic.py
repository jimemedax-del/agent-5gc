"""Bounded traffic-job facade. The VM1 worker owns task lifetime and locks."""
import copy
import json
import math
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import uuid

TERMINAL = {'completed', 'stopped', 'failed', 'interrupted'}
ENVIRONMENTS = {'cnf-lab', 'cnf-multi-ue'}


def normalize(payload):
    if not isinstance(payload, dict) or set(payload) != {'environment','durationSeconds','flows'}:
        raise ValueError('请提供环境、持续时间和流量列表；不接受额外字段。')
    environment = payload['environment']
    if not isinstance(environment,str) or environment not in ENVIRONMENTS:
        raise ValueError('请选择基础服务链或双用户实验。')
    duration = payload['durationSeconds']
    if type(duration) is not int or not 10 <= duration <= 300:
        raise ValueError('持续时间必须为 10–300 的整数秒。')
    flows = payload['flows']
    if not isinstance(flows,list) or not 1 <= len(flows) <= 4:
        raise ValueError('每个任务支持 1–4 条并发流。')
    total, targets, output = 0, set(), []
    for i, flow in enumerate(flows):
        if not isinstance(flow,dict) or set(flow) != {'ue','target','direction','rateMbps'}:
            raise ValueError('第 %d 条流参数不完整或包含额外字段。'%(i+1))
        for key, values in [('ue',('ue1','ue2')),('target',('core','edge')),('direction',('ul','dl'))]:
            if not isinstance(flow[key],str) or flow[key] not in values:
                raise ValueError('第 %d 条流的用户、目标或方向无效。'%(i+1))
        speed = flow['rateMbps']
        if type(speed) not in (int,float) or not .1 <= speed <= 4 or not math.isfinite(speed):
            raise ValueError('每流目标发送速率必须为 0.1–4 Mbps。')
        speed = round(speed,3)
        total += speed
        target = (flow['ue'] if environment=='cnf-multi-ue' else 'shared', flow['target'])
        if target in targets:
            raise ValueError('同一个测试后端只能承载一条流；基础服务链的后端由两个 UE 共享。')
        targets.add(target)
        output.append(dict(flow,rateMbps=speed))
    if total > 4.000001:
        raise ValueError('任务目标发送速率之和不能超过 4 Mbps。')
    return {'environment':environment, 'durationSeconds':duration, 'flows':output}


def check_routes(config, data):
    """Refuse a label-to-port mismatch in the current mounted proxy config."""
    targets = set()
    for flow in config['flows']:
        if config['environment']=='cnf-lab':
            port = 15201 if flow['target']=='core' else 15202
            name = 'cnf-m1-backend' if flow['target']=='core' else 'cnf-m2-edge'
        else:
            port = 15301 + int(flow['ue']=='ue2') + (2 if flow['target']=='edge' else 0)
            name = 'cnf-a-'+flow['target']+'-'+flow['ue']
        sid = 'svc/'+config['environment']+'/'+name
        matches = [r for r in data.get('routes',[]) if r['namespace']==config['environment'] and r['port']==port]
        if len(matches)!=1 or matches[0].get('target')!=sid or not matches[0].get('resolved'):
            raise ValueError('业务标签与当前代理配置不一致，请先核对监听端口及后端。')
        if any(t.get('port')!=5201 for t in matches[0].get('targets',[])):
            raise ValueError('代理后端端口已变化，拒绝使用旧测试映射。')
        service = next((s for s in data.get('services',[]) if s['id']==sid),None)
        endpoints = [e for e in service.get('endpoints',[]) if e.get('ready') is True] if service else []
        if len(endpoints)!=1 or len(endpoints[0].get('addresses',[]))!=1:
            raise ValueError('所选后端没有唯一 Ready 端点。')
        target = endpoints[0]['addresses'][0]+':5201'
        if target in targets:
            raise ValueError('两条流映射到同一真实后端，不能并发测试。')
        targets.add(target)


class Traffic:
    def __init__(self, store):
        self.store = store
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.operation = threading.Lock()
        self.cache = {'jobs':[], 'error':None, 'observedAt':None, 'available':False}
        self.wake = threading.Event()
        self.shutdown = threading.Event()

    def rpc(self, request):
        source = Path(__file__).with_name('traffic_remote.py').read_text(encoding='utf-8')
        program = 'SOURCE = '+repr(source)+'\n'+source+'\nprint(json.dumps(dispatch('+repr(request)+', SOURCE), ensure_ascii=False))\n'
        if self.store.source=='replay':
            raise ValueError('归档回放模式不能发送或停止真实流量。')
        command = ['python3','-']
        if self.store.source=='ssh':
            command = ['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=8',
                '-o','ServerAliveInterval=8','-o','ServerAliveCountMax=2',self.store.host,'python3','-']
        options = {'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform=='win32' else {}
        result = subprocess.run(command,input=program,capture_output=True,text=True,
            encoding='utf-8',errors='replace',timeout=55,**options)
        if result.returncode:
            message = result.stderr.strip().splitlines()[-1][-500:] if result.stderr.strip() else '任务服务暂不可用'
            for prefix in ('RuntimeError: ', 'ValueError: '):
                if message.startswith(prefix):
                    message = message[len(prefix):]
            raise RuntimeError(message)
        return json.loads(result.stdout)

    def refresh(self):
        if not self.operation.acquire(blocking=False):
            return
        try:
            if self.store.source=='replay':
                with self.lock:
                    self.cache.update(available=False,error='归档回放仅供观察，禁止发流。')
                return
            try:
                value = self.rpc({'action':'list'})
                with self.lock:
                    self.cache = dict(value, error=None,available=True,observedAt=time.time())
            except Exception as exc:
                # Never turn unknown liveness into completed or free the lock.
                with self.lock:
                    self.cache.update(error=str(exc)[:600],available=False)
        finally:
            self.operation.release()

    def state(self):
        with self.lock:
            return dict(self.cache, token=self.token, protocol='TCP', maxTotalMbps=4,
                segmentSeconds=45, minSeconds=10, maxSeconds=300)

    def start(self, payload):
        config = normalize(payload)
        snapshot = self.store.state()
        data = snapshot.get('data')
        if self.store.source=='replay' or not data or snapshot.get('error'):
            raise ValueError('当前没有可用的真实集群观测，不能启动测试。')
        from .model import fresh
        import datetime as dt
        if not fresh(data.get('observedAt'),dt.datetime.now(dt.timezone.utc).isoformat(),60):
            raise ValueError('集群观测已超过 60 秒，请刷新后再启动。')
        if any(c['status']=='fail' for c in data.get('checks',[]) if c['key'] in ('nodes','pods','api')):
            raise ValueError('集群健康检查有异常，请先处理。')
        check_routes(config, data)
        with self.operation:
            with self.lock:
                if any(j['status'] not in TERMINAL for j in self.cache['jobs']):
                    raise RuntimeError('已有任务运行或状态未确认，请先停止或恢复原任务。')
            result = self.rpc({'action':'start','id':uuid.uuid4().hex,'config':config})
            with self.lock:
                self.cache['jobs'].insert(0,result['job'])
                self.cache.update(error=None,available=True,observedAt=time.time())
        self.wake.set()
        return result

    def stop(self, job_id):
        if not isinstance(job_id,str) or len(job_id)!=32 or any(c not in '0123456789abcdef' for c in job_id):
            raise ValueError('任务标识无效。')
        with self.operation:
            result = self.rpc({'action':'stop','id':job_id})
        self.wake.set()
        return result

    def report(self, job_id):
        if not isinstance(job_id,str) or len(job_id)!=32 or any(c not in '0123456789abcdef' for c in job_id):
            raise ValueError('任务标识无效。')
        with self.lock:
            known = next((j for j in self.cache['jobs'] if j['id']==job_id),None)
            if known:
                # Export the exact evidence already displayed, including its
                # provenance. SSH failure must not erase an existing report.
                return {'job':copy.deepcopy(known),'observedAt':self.cache['observedAt'],
                        'cached':True,'source':'VM1 last confirmed task observation',
                        'connectionError':self.cache['error']}
        with self.operation:
            return self.rpc({'action':'get','id':job_id})

    def loop(self):
        while not self.shutdown.is_set():
            self.wake.clear()
            self.refresh()
            active = any(j['status'] not in TERMINAL for j in self.cache['jobs'])
            self.wake.wait(2 if active else 8)
