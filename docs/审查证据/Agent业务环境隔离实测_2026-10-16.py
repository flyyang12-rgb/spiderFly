from pathlib import Path
import base64,hashlib,json,os,sys,time,uuid
from unittest.mock import patch
root=Path(__file__).parent
assert root.name.startswith("spiderfly-check-") and not (root/".env").exists(), "Requires a fresh isolated project with synthetic UI fixture"
for key in tuple(os.environ):
 if key.upper().startswith(('SPIDERFLY_','FEISHU_','LARK_','DEEPSEEK_','OPENAI_')) or key.upper() in ('PYTHONPATH','PYTHONHOME'):os.environ.pop(key)
sys.path.insert(0,str(root/'agent'))
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from spiderfly_agent.api import Api,ApiError
from spiderfly_agent.worker import Worker
from spiderfly_agent.journal import Journal
from spiderfly_agent.client import Client
server='http://127.0.0.1:18763'; credentials=json.loads((root/'ui-credentials.json').read_text())
admin=requests.Session();admin.trust_env=False
login=admin.post(server+'/api/auth/login',json={'username':credentials['username'],'password':credentials['password']},headers={'Origin':server});assert login.status_code==200,login.status_code
admin.headers['Origin']=server

def manage(method,path,body=None,**kw):
 if body is not None:kw['json']=body
 response=admin.request(method,server+path,timeout=20,**kw)
 assert response.ok,(response.status_code,response.text[:300])
 return response.json()
key=Ed25519PrivateKey.generate();api=Api(server,key)
code=manage('POST','/api/hosts/enrollment-codes',{})['code']
registered=api.request('POST','/api/agent/register',{'enrollment_code':code,'machine_id':'synthetic-'+uuid.uuid4().hex,'name':'阶段C验收电脑','public_key':base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode(),'agent_version':'0.4.0','protocol_version':1},signed=False)
api.host_id=registered['host_id'];host=api.host_id
manage('POST',f'/api/hosts/{host}/approve',{});manage('PATCH',f'/api/hosts/{host}/mode',{'dispatch_enabled':True})
agentroot=root/'synthetic-agent-final';agentroot.mkdir(exist_ok=True)
journal=Journal(agentroot/'journal.sqlite3')

def heartbeat(active=None):
 return api.request('POST','/api/agent/heartbeat',{'protocol_version':1,'agent_version':'0.4.0','dispatch_enabled':True,'interactive_session':True,'active_run_id':active,'recovery_required':False})
heartbeat()
source="import os\nimport requests\nfrom pathlib import Path\nprint('synthetic requests version:', requests.__version__)\nPath(os.environ['SPIDERFLY_ARTIFACT_DIR'],'proof.txt').write_text(requests.__version__)\n"
def create(name,script):
 value=manage('POST','/api/apps',files={'script':('proof.py',script.encode(),'text/x-python')},data={'name':name+' '+str(host),'requirements_text':'requests==2.32.5','target_host_id':host,'trigger_type':'manual','enabled':'true','notify_on_success':'false','notify_on_failure':'false','request_id':uuid.uuid4().hex})
 return value['task']['id']
task=create('Agent环境验收',source)
results=[]

def run_task(task,label,lose_ack=False,bad_source=False):
 global journal
 run=manage('POST',f'/api/hosts/{host}/runs',{'task_id':task,'request_id':str(uuid.uuid4())})
 # A queued run remains on this target until the real signed Agent heartbeat.
 assert run['host_id']==host and run['status']=='queued'
 offer=heartbeat()['run'];assert offer['id']==run['id'];journal.claim(offer)
 claim=api.request('POST',f'/api/agent/runs/{run["id"]}/claim')
 worker=Worker(claim,agentroot,api,journal)
 worker.run()
 original=api.request
 if lose_ack:
  def lose(method,path,*a,**kw):
   response=original(method,path,*a,**kw)
   if path.endswith('/events'):raise ApiError(503,'synthetic response lost after controller committed')
   return response
  with patch.object(api,'request',side_effect=lose):
   try:Client(api,journal,agentroot).flush()
   except ApiError:pass
  assert journal.active()['ack_seq']==0
  journal.close();journal=Journal(agentroot/'journal.sqlite3')
 client=Client(api,journal,agentroot,session_check=lambda:True)
 for i in range(15):
  client.tick()
  if journal.active() is None:break
 assert journal.active() is None
 detail=manage('GET',f'/api/remote-runs/{run["id"]}')
 facts=detail['environment'];assert facts and facts['package_hash']==run['package_hash']
 assert facts['requirements_hash']==hashlib.sha256(b'requests==2.32.5').hexdigest()
 item={'label':label,'run_id':run['id'],'status':detail['status'],'environment':facts,
       'event_kinds':[e['kind'] for e in detail['events']],'artifact_count':len(detail['artifacts']),'lost_ack_replayed':lose_ack}
 results.append(item)
 print(label,detail['status'],facts['status'],facts['stage'],flush=True)
 return detail
first=run_task(task,'first-install-and-durable-replay',lose_ack=True)
assert first['status']=='succeeded' and first['environment']['cache_reused'] is False
second=run_task(task,'cached-environment-rechecked')
assert second['status']=='succeeded' and second['environment']['cache_reused'] is True
cache=agentroot/'envs'/first['package_hash'][:32]
site=cache/'Lib/site-packages';broken=site/'synthetic_conflict-1.0.dist-info';broken.mkdir()
(broken/'METADATA').write_text('Metadata-Version: 2.1\nName: synthetic-conflict\nVersion: 1.0\nRequires-Dist: definitely-missing-synthetic-dependency>=1\n','utf-8')
failed=run_task(task,'real-pip-check-conflict')
assert failed['status']=='failed' and failed['environment']['stage']=='pip_check' and 'started' not in results[-1]['event_kinds']
assert not (cache/'.ready').exists() and not failed['artifacts']
retry=run_task(task,'unfinished-cache-rebuilt-and-rechecked')
assert retry['status']=='succeeded' and retry['environment']['cache_reused'] is False
secondtask=create('Agent安装失败验收',source+'\n# separate frozen package\n')
with patch.dict(os.environ,{'PIP_INDEX_URL':'http://127.0.0.1:9/simple','PIP_RETRIES':'0','PIP_TIMEOUT':'2'}):
 network=run_task(secondtask,'real-unreachable-package-source')
assert network['status']=='failed' and network['environment']['stage']=='install' and 'started' not in results[-1]['event_kinds']
assert not network['artifacts']
manage('PATCH',f'/api/hosts/{host}/mode',{'dispatch_enabled':False});heartbeat()
for taskid in (task,secondtask):
 manage('PATCH',f'/api/tasks/{taskid}',{'enabled':False})
summary={'kind':'single-Windows synthetic controller plus real Agent Worker, real signed HTTP, venv/pip/probe; not two-PC acceptance','host_id':host,'task_id':task,'results':results,'active_journal':journal.active(),'external_models':0,'real_notifications':0}
(root/'real-agent-environment.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),'utf-8')
journal.close()
print('PROOF COMPLETE',flush=True)