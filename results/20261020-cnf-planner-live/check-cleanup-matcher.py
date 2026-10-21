from pathlib import Path
import importlib.util,subprocess,json
root=Path('/home/lhm/.work/cnf-e2-20261020')
spec=importlib.util.spec_from_file_location('e2_cleanup',root/'scripts/cnf-planner-live.py')
x=importlib.util.module_from_spec(spec);spec.loader.exec_module(x)
token='c'*24
owned=['iperf3','-c','import time; time.sleep(20)','--extra-data',token]
other=['iperf3','-c','import time; time.sleep(20)','--extra-data','d'*24]
jobs=[]
try:
    for command in [owned,other]: jobs.append(subprocess.Popen(command,executable='/usr/bin/python3'))
    def native(*args,**kwargs):
        return subprocess.run(args[args.index('--')+1:],capture_output=True,text=True,timeout=5)
    x.c.m1.kube=native
    result=x.stop_owned_process('synthetic-pod',['kubectl','-n','free5gc','exec','synthetic-pod','--',*owned],token)
    jobs[0].wait(timeout=5)
    proof={'syntheticHostProcessTest':True,'scope':'GNU exact-command matching only; no UE processes or network mutation',
           'ownedTerminated':jobs[0].returncode == -15,'unrelatedAlive':jobs[1].poll() is None,'pkillReturncode':result.returncode}
    x.save(root/'results/cleanup-matcher-test.json',proof);print(json.dumps(proof),flush=True)
    assert proof['ownedTerminated'] and proof['unrelatedAlive'] and result.returncode==0
finally:
    for job in jobs:
        if job.poll() is None: job.terminate()
        job.wait(timeout=5)
