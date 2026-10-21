import os,json,subprocess,fcntl,datetime,pathlib
os.environ['KUBECONFIG']='/home/lhm/.kube/config'
def kube(args):
 r=subprocess.run(['kubectl',*args],capture_output=True,text=True,timeout=15)
 if r.returncode: raise RuntimeError(r.stderr)
 return json.loads(r.stdout)
def snapshot():
 return {p['metadata']['namespace']+'/'+p['metadata']['name']:{'uid':p['metadata']['uid'],'restarts':sum(c.get('restartCount',0) for c in p.get('status',{}).get('containerStatuses',[]))} for p in kube(['get','pods','-A','-o','json'])['items']}
lock=open(pathlib.Path.home()/'.work/cnf-planner-live.lock','a')
fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
before=snapshot()
pods=kube(['-n','free5gc','get','pods','-o','json'])['items']
ue=next(p for p in pods if p['metadata']['name'].startswith('ueransim-ue-'))
name=ue['metadata']['name']
addresses=kube(['-n','free5gc','exec',name,'--','ip','-j','address','show','uesimtun0'])
source=next(a['local'] for a in addresses[0]['addr_info'] if a['family']=='inet')
service=kube(['-n','cnf-lab','get','svc','cnf-m1-envoy','-o','json'])
port=next(p['nodePort'] for p in service['spec']['ports'] if p['port']==15201)
node=next(n for n in kube(['get','nodes','-o','json'])['items'] if n['metadata'].get('labels',{}).get('5gc.free5gc.org/plane')=='control')
ip=next(a['address'] for a in node['status']['addresses'] if a['type']=='InternalIP')
output={'scope':'read-only-dashboard-validation; two bounded 1-Mbps TCP smoke flows; no policy/configuration changes','before':before,'flows':[]}
for direction,extra in [('uplink',[]),('downlink',['-R'])]:
 started=datetime.datetime.now(datetime.timezone.utc).isoformat()
 command=['kubectl','-n','free5gc','exec',name,'--','iperf3','-c',ip,'-p',str(port),'-B',source,'-b','1M','-t','20','-l','16384','--connect-timeout','5000','-J',*extra]
 p=subprocess.run(command,capture_output=True,text=True,timeout=35)
 output['flows'].append({'direction':direction,'startedAt':started,'source':source,'nodePort':port,'command':command,'returncode':p.returncode,'result':json.loads(p.stdout) if p.stdout else {},'stderr':p.stderr})
output['after']=snapshot()
output['identitiesAndRestartsUnchanged']=output['before']==output['after']
print(json.dumps(output,ensure_ascii=False))
