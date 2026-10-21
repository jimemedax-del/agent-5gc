from pathlib import Path
import importlib.util,json,time
root=Path('/home/lhm/.work/cnf-e2-20261020')
spec=importlib.util.spec_from_file_location('e2',root/'scripts/cnf-planner-live.py')
x=importlib.util.module_from_spec(spec);spec.loader.exec_module(x)
folder=root/'results/e2-inspect-01'
state=x.read(folder/'live-state.json')
with x.Backend() as b:
    try:
        with x.Backend(): raise RuntimeError('lock was not exclusive')
    except x.p.Rejection as exc:
        x.need(exc.code == 'live_adapter_busy','lock_test','unexpected lock failure')
    b.topology=x.read(folder/'live/c1/topology.json');b.pod=b.topology['contention']['pod']
    start=time.monotonic();observed=b.observe(folder/'stability-recheck')
    stable=x.digest(observed)==state['fingerprint']
    proof={'stable':stable,'observeSeconds':time.monotonic()-start,'lockConflictRejected':True,'podCount':sum(map(len,observed['pods'].values())),'checkedAt':x.now().isoformat()}
    x.save(folder/'stability.json',proof);print(json.dumps(proof),flush=True)
    x.need(stable,'preflight_drift','Read-only observations differ')

