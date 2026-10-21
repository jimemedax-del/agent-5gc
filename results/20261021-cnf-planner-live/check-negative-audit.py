from pathlib import Path
import importlib.util,json,shutil,tempfile
folder=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('fault_audit',folder.parents[1]/'scripts/audit-cnf-planner-live-faults.py')
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
source=folder/'faults-01'

def mutate(path,change):
    value=json.loads(path.read_text(encoding='utf-8'));path.write_text(json.dumps(change(value)),encoding='utf-8')

cases=[
('hidden_failed_run','expired-plan/run/summary.json',lambda v:{**v,'outcome':'passed_controlled_scope'}),
('missing_expiration','expired-plan/fault.json',lambda v:{**v,'waitEndedAt':v['waitStartedAt']}),
('wrong_rejection','queue-drift/run/execution/rejection.json',lambda v:{**v,'errorCode':'expired_plan'}),
('erased_other_actor_queue','queue-drift/fixture-teardown/tc-before.json',lambda v:{**v,'qdisc':[]}),
('incomplete_partial_write','partial-write/writes.json',lambda v:[]),
('false_final_baseline','partial-write/run/final/observed/observation.json',lambda v:{**v,'resources':[]}),
('unowned_interrupted_process','measurement-interrupted/fault.json',lambda v:{**v,'token':'f'*24}),
('failed_cleanup_hidden','measurement-interrupted/run/measurement/cleanup.json',lambda v:{**v,'errors':['stop failed']}),
('residual_process','measurement-interrupted/run/final/observed/process-checks.json',lambda v:[{**p,'returncode':0} for p in v]),
('normal_receiver_cookie','normal-control/measurement/ue1/iperf.json',lambda v:{**v,'start':{**v['start'],'cookie':'wrong-cookie'}}),
]
checks=[]
for name,relative,change in cases:
    with tempfile.TemporaryDirectory() as tmp:
        target=Path(tmp)/'evidence';shutil.copytree(source,target)
        mutate(target/relative,change)
        try:a.audit(target)
        except Exception as exc:checks.append({'case':name,'rejected':True,'reason':str(exc)})
        else:checks.append({'case':name,'rejected':False})
with tempfile.TemporaryDirectory() as tmp:
    target=Path(tmp)/'evidence';shutil.copytree(source,target)
    path=target/'inputs/scripts/cnf-planner-live.py';path.write_text(path.read_text(encoding='utf-8')+'\n# changed\n',encoding='utf-8')
    try:a.audit(target)
    except Exception as exc:checks.append({'case':'changed_source','rejected':True,'reason':str(exc)})
    else:checks.append({'case':'changed_source','rejected':False})
value={'scope':'offline_corrupted_evidence_checks','checks':checks,'allRejected':all(v['rejected'] for v in checks)}
(folder/'negative-audit-checks.json').write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8');print(json.dumps(value));assert value['allRejected']
