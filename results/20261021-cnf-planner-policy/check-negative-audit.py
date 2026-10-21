from pathlib import Path
import importlib.util,json,shutil,tempfile
folder=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('e3_audit_checks',folder.parents[1]/'scripts/audit-cnf-planner-policy.py')
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
spec=importlib.util.spec_from_file_location('e3_seal',folder.parents[1]/'scripts/cnf-planner-policy.py')
t=importlib.util.module_from_spec(spec);spec.loader.exec_module(t)
source=folder/'evaluation-01'

def mutate(path,fn):
    value=json.loads(path.read_text(encoding='utf-8'));path.write_text(json.dumps(fn(value)),encoding='utf-8')

cases=[
('modified_caller_request','fixture-requests/equal.json',lambda v:{**v,'protocol':'TCP'}),
('changed_expected_role','cases.json',lambda v:[{**c,'mode':'guarantee-ue2'} if c['name']=='guarantee-ue1' else c for c in v]),
('receiver_cookie','guarantee-ue1/measurement/ue1/iperf.json',lambda v:{**v,'start':{**v['start'],'cookie':'wrong-cookie'}}),
('wrong_restore_shape','guarantee-ue2/restore/tc-before.json',lambda v:{**v,'class':[]}),
('failed_cleanup_hidden','equal/measurement/cleanup.json',lambda v:{**v,'errors':['cleanup failed']}),
('rejected_but_written','missing-priority/summary.json',lambda v:{**v,'mutationAttempted':True}),
('changed_final_identity','guarantee-ue1/final/observed/observation.json',lambda v:{**v,'resources':[]}),
('wrong_selected_mode','guarantee-ue2/execution.json',lambda v:{**v,'mode':'guarantee-ue1'}),
('forged_expiry','equal/execution/write-intent.json',lambda v:{**v,'startedAt':'2099-01-01T00:00:00+00:00'}),
]
checks=[]
for name,relative,fn in cases:
    with tempfile.TemporaryDirectory() as tmp:
        target=Path(tmp)/'evidence';shutil.copytree(source,target)
        mutate(target/relative,fn)
        try:a.audit(target)
        except Exception as exc:checks.append({'case':name,'rejected':True,'reason':str(exc)})
        else:checks.append({'case':name,'rejected':False})
with tempfile.TemporaryDirectory() as tmp:
    target=Path(tmp)/'evidence';shutil.copytree(source,target)
    path=target/'equal/plan.json';plan=json.loads(path.read_text(encoding='utf-8'));plan['selectionRule']='unrestricted-selection'
    plan=t.p.seal({k:v for k,v in plan.items() if k not in ['policyPlanId','policyPlanHash']},'policyPlan')
    path.write_text(json.dumps(plan),encoding='utf-8')
    mutate(target/'equal/planning.json',lambda v:{**v,'plan':plan})
    try:a.audit(target)
    except Exception as exc:checks.append({'case':'rehashed_selection_semantics','rejected':True,'reason':str(exc)})
    else:checks.append({'case':'rehashed_selection_semantics','rejected':False})
with tempfile.TemporaryDirectory() as tmp:
    target=Path(tmp)/'evidence';shutil.copytree(source,target)
    path=target/'inputs/scripts/cnf-planner-policy.py';path.write_text(path.read_text(encoding='utf-8')+'\n# changed\n',encoding='utf-8')
    try:a.audit(target)
    except Exception as exc:checks.append({'case':'changed_archived_source','rejected':True,'reason':str(exc)})
    else:checks.append({'case':'changed_archived_source','rejected':False})
value={'scope':'offline_corrupted_evidence_checks','checks':checks,'allRejected':all(c['rejected'] for c in checks)}
(folder/'negative-audit-checks.json').write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8');print(json.dumps(value));assert value['allRejected']
