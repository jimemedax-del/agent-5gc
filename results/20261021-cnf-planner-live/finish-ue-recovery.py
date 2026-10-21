from pathlib import Path
import importlib.util,time,json
root=Path('/home/lhm/.work/cnf-e2-faults-20261021')
spec=importlib.util.spec_from_file_location('finish',root/'scripts/cnf-planner-live.py')
x=importlib.util.module_from_spec(spec);spec.loader.exec_module(x)
folder=root/'results/ue-recovery';attempts=[];deadline=time.monotonic()+60
while True:
    try:after=x.c2.snapshot();break
    except RuntimeError as exc:
        attempts.append({'at':x.now().isoformat(),'error':str(exc)});x.save(folder/'stabilization-attempts.json',attempts)
        x.need(time.monotonic()<deadline,'termination_timeout','Old UE Pod did not disappear')
        time.sleep(1)
before=x.read(folder/'pods-before.json');x.save(folder/'pods-after.json',after)
changed=[name for ns,pods in before.items() for name,pod in pods.items() if name not in after[ns] or after[ns][name]['uid']!=pod['uid']]
x.need(len(changed)==2 and all(name.startswith(('ueransim-ue-','cnf-a-ue2-')) for name in changed),'unexpected_recovery_drift',str(changed))
retained={ns:{n:p for n,p in pods.items() if n not in changed} for ns,pods in before.items()}
selected={ns:{n:after[ns][n] for n in pods} for ns,pods in retained.items()}
x.c.m1.assert_steady(retained,selected)
x.save(folder/'summary.json',{'outcome':'recovered_before_formal_tests','changedOriginalPodNames':changed,'other24PodsUnchanged':True,
      'initialRecoveryPostSnapshot':'temporarily blocked by terminating old UE Pod; no additional restart', 'completedAt':x.now().isoformat()})
with x.Backend() as b:
    out=root/'results/preflight-02';out.mkdir();x.archive_inputs(out)
    result=x.inspect_state(b,out/'live');x.save(out/'live-state.json',result['state'])
    print(json.dumps({'strictPreflight':result['status'],'state':result['state']['liveStateId']}),flush=True)
