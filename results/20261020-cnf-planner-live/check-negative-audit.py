from pathlib import Path
import importlib.util,json,shutil,tempfile,copy
root=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('audit_e2',root.parents[1]/'scripts/audit-cnf-planner-live.py')
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
live_spec=importlib.util.spec_from_file_location('current_e2',root.parents[1]/'scripts/cnf-planner-live.py')
x=importlib.util.module_from_spec(live_spec);live_spec.loader.exec_module(x)
source=root/'e2-run-02'

def mutate(folder,name,change):
    path=folder/name
    value=json.loads(path.read_text(encoding='utf-8'))
    value=change(value)
    path.write_text(json.dumps(value),encoding='utf-8')

def bad_inner(v):
    v['innerPlan']['action']['namespace']='free5gc'
    v['innerPlan']=x.p.seal({k:w for k,w in v['innerPlan'].items() if k not in ['planId','planHash']},'plan')
    return x.p.seal({k:w for k,w in v.items() if k not in ['livePlanId','livePlanHash']},'livePlan')

def bad_state(v):
    v['validUntil']=(x.p.instant(v['observedAt'])+x.timedelta(seconds=300)).isoformat()
    return x.p.seal({k:w for k,w in v.items() if k not in ['liveStateId','liveStateHash']},'liveState')

cases=[
('changed_request','requests.json',lambda v:{**v,'protocol':'TCP'}),
('rehashed_foreign_action','plan.json',bad_inner),
('rehashed_extended_freshness','live-state.json',bad_state),
('expired_validation','execution/validation.json',lambda v:{**v,'checkedAt':(x.p.instant(v['checkedAt'])+x.timedelta(seconds=60)).isoformat()}),
('wrong_write_endpoint','execution/write-intent.json',lambda v:{**v,'endpoint':{'uid':'replacement'}}),
('receiver_cookie','measurement/ue1/iperf.json',lambda v:{**v,'start':{**v['start'],'cookie':'tampered-cookie'}}),
('missing_restore_success','restoration.json',lambda v:{**v,'originalObservedStateMatched':False}),
('changed_final_resources','final/observed/observation.json',lambda v:{**v,'resources':[]}),
('changed_measurement_pods','measurement/guard-pods.json',lambda v:{**v,'cnf-contention':{}}),
('changed_restored_tc','restore/tc-after.json',lambda v:{**v,'qdisc':[]}),
]
checks=[]
for name,relative,change in cases:
    with tempfile.TemporaryDirectory() as temp:
        folder=Path(temp)/'run';shutil.copytree(source,folder)
        mutate(folder,relative,change)
        try:
            a.audit(folder)
        except Exception as exc:
            checks.append({'case':name,'rejected':True,'reason':str(exc)})
        else:
            checks.append({'case':name,'rejected':False})
with tempfile.TemporaryDirectory() as temp:
    folder=Path(temp)/'run';shutil.copytree(source,folder)
    path=folder/'inputs/scripts/cnf-planner-live.py'
    path.write_text(path.read_text(encoding='utf-8')+'\n# altered\n',encoding='utf-8')
    try:
        a.audit(folder)
    except Exception as exc:
        checks.append({'case':'changed_archived_source','rejected':True,'reason':str(exc)})
    else: checks.append({'case':'changed_archived_source','rejected':False})
value={'checks':checks,'allRejected':all(v['rejected'] for v in checks),'scope':'offline_corrupted_evidence_checks; not live failure injection'}
(root/'negative-audit-checks.json').write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
print(json.dumps(value));assert value['allRejected']
