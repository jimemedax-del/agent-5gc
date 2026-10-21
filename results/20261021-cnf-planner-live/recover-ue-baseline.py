from pathlib import Path
import importlib.util,json,time
root=Path('/home/lhm/.work/cnf-e2-faults-20261021')
spec=importlib.util.spec_from_file_location('recover',root/'scripts/cnf-planner-live.py')
x=importlib.util.module_from_spec(spec);spec.loader.exec_module(x)
folder=root/'results/ue-recovery';folder.mkdir()
before=x.c2.snapshot();x.save(folder/'pods-before.json',before)
for label,deployment,supi in [('ue1','ueransim-ue','imsi-208930000000001'),('ue2','cnf-a-ue2','imsi-208930000000002')]:
    r=x.c.m1.kube('-n','free5gc','rollout','restart','deployment/'+deployment)
    x.save(folder/(label+'-restart.txt'),r.stdout)
    x.save(folder/(label+'-rollout.txt'),x.c.m1.kube('-n','free5gc','rollout','status','deployment/'+deployment,'--timeout=60s',timeout=65).stdout)
    attempts=[];deadline=time.monotonic()+60
    while True:
        attempt={'at':x.now().isoformat()}
        try:
            status=x.c.m1.kube('-n','free5gc','exec','deployment/'+deployment,'--','./nr-cli',supi,'-e','status',timeout=5)
            sessions=x.c.m1.kube('-n','free5gc','exec','deployment/'+deployment,'--','./nr-cli',supi,'-e','ps-list',timeout=5)
            ping=x.c.m1.kube('-n','free5gc','exec','deployment/'+deployment,'--','ping','-I','uesimtun0','-c','3','-i','.2','-W','2','192.168.244.128',required=False,timeout=10)
            attempt.update(status=status.stdout,sessions=sessions.stdout,pingReturncode=ping.returncode,ping=ping.stdout)
            passed='rm-state: RM-REGISTERED' in status.stdout and sessions.stdout.count('PDU Session')==1 and 'state: PS-ACTIVE' in sessions.stdout and ping.returncode==0
        except Exception as exc:attempt['error']=str(exc);passed=False
        attempts.append(attempt);x.save(folder/(label+'-attempts.json'),attempts)
        if passed:break
        x.need(time.monotonic()<deadline,'recovery_timeout',label)
        time.sleep(1)
    print(json.dumps({'recovered':label,'attempts':len(attempts)}),flush=True)
after=x.c2.snapshot();x.save(folder/'pods-after.json',after)
changed=[name for ns,pods in before.items() for name,pod in pods.items() if name not in after[ns] or after[ns][name]['uid']!=pod['uid']]
x.need(len(changed)==2 and all(name.startswith(('ueransim-ue-','cnf-a-ue2-')) for name in changed),'unexpected_recovery_drift',str(changed))
x.save(folder/'summary.json',{'outcome':'recovered_before_formal_tests','changedOriginalPodNames':changed,'other24PodsUnchanged':True,'completedAt':x.now().isoformat()})
with x.Backend() as b:
    out=root/'results/preflight-02';out.mkdir();x.archive_inputs(out)
    result=x.inspect_state(b,out/'live');x.save(out/'live-state.json',result['state'])
    print(json.dumps({'strictPreflight':result['status'],'state':result['state']['liveStateId']}),flush=True)
