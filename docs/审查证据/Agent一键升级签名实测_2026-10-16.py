"""Local-only real HTTP, DPAPI identity, package and short-lived transport proof."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,threading,time,uuid,zipfile
root=Path(__file__).resolve().parent
assert not(root/'.env').exists()
for key in tuple(os.environ):
 if key.upper().startswith(('SPIDERFLY_','FEISHU_','LARK_','DEEPSEEK_','OPENAI_')) or key.upper() in ('PYTHONPATH','PYTHONHOME','PSMODULEPATH'):os.environ.pop(key)
sys.path[:0]=[str(root/'backend'),str(root/'agent')]
from app import config,security,main
assert config.PROJECT_ROOT==root
for name in ('DATA_DIR','RPA_APPS_DIR','RPA_ENVS_DIR','EXECUTIONS_DIR','WORK_DIR'):assert getattr(config,name).resolve().is_relative_to(root)
from tests.test_hosts import HostApiTests
from app.api import hosts
from spiderfly_agent.identity import load_identity,save_json
from spiderfly_agent.journal import Journal
import requests,uvicorn
case=HostApiTests();case.setUp()
server=None;thread=None
try:
 state=case.root/'Real DPAPI identity';state.mkdir()
 machine,key=load_identity(state)
 payload,_=case.registration(key=key,machine_id=machine)
 registered=case.success(case.request('POST','/api/agent/register',payload,user=None));host=registered['host_id'];case.keys[host]=key
 case.success(case.request('POST',f'/api/hosts/{host}/approve'))
 case.success(case.request('PATCH',f'/api/hosts/{host}/mode',{'dispatch_enabled':True}));case.heartbeat(host,agent_version='0.4.0')
 # No lifespan: this synthetic HTTP server has no scheduler/queue workers.
 server=uvicorn.Server(uvicorn.Config(main.app,host='127.0.0.1',port=18763,lifespan='off',log_level='error'))
 thread=threading.Thread(target=server.run,daemon=True);thread.start()
 deadline=time.monotonic()+10
 while not server.started and time.monotonic()<deadline:time.sleep(.03)
 assert server.started
 save_json(state/'config.json',{'server':'http://127.0.0.1:18763','host_id':host,'name':'Synthetic updater'})
 journal=Journal(state/'journal.sqlite3');journal.close()
 before={n:(state/n).read_bytes() for n in ('identity.json','config.json')}
 session=requests.Session();session.trust_env=False
 try:response=session.get('http://127.0.0.1:18763/api/hosts/agent-download',cookies={security.SESSION_COOKIE_NAME:case.tokens['admin']},timeout=10)
 finally:session.close()
 assert response.status_code==200
 archive=root/'controller-agent-0.4.1.zip';archive.write_bytes(response.content)
 with zipfile.ZipFile(archive) as z:
  manifest=json.loads(z.read('SpiderFlyAgent/update-manifest.json'));assert manifest['version']=='0.4.1'
  for name,digest in manifest['files'].items():assert hashlib.sha256(z.read('SpiderFlyAgent/'+name)).hexdigest()==digest
  files=len(z.namelist())
 def transport(action,token=None):
  args=[sys.executable,'-I',str(root/'agent/upgrade-transport.py'),action,'--data-dir',str(state)]
  if token:args+=['--token',token]
  result=subprocess.run(args,capture_output=True,timeout=20)
  assert result.returncode==0,result.stderr.decode('utf-8','replace')
  return json.loads(result.stdout)
 assert transport('inspect')['unacknowledged']==0
 # A real helper error must remain readable across Python/PowerShell encoding.
 literal=lambda p:"'"+str(p).replace("'","''")+"'"
 script="[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)\n. "+literal(root/'agent/upgrade-core.ps1')+"\ntry{Invoke-UpgradeTransport "+literal(sys.executable)+' '+literal(root/'agent/upgrade-transport.py')+' '+literal(case.root/'Not registered')+" 'inspect' '';throw 'Unexpected helper success'}catch{$_.Exception.Message|ConvertTo-Json}\n"
 driver=root/'check-helper-error.ps1';driver.write_text(script,'utf-8-sig')
 checked=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(driver)],capture_output=True,timeout=20)
 assert checked.returncode==0
 assert '请选择已接入的 Agent 数据目录' in checked.stdout.decode('utf-8-sig')
 token=uuid.uuid4().hex
 assert transport('pause',token)['paused']
 run=case.queue(host)
 paused=case.heartbeat(host,agent_version='0.4.0');assert paused['run'] is None and paused['host']['state']=='upgrading'
 assert not transport('resume',token)['paused']
 assert not transport('resume',token)['paused']
 assert not case.host(host)['upgrade_pending']
 for name,value in before.items():assert (state/name).read_bytes()==value
 result={'real_http':True,'real_dpapi_identity':True,'real_short_lived_transport':True,'helper_error_readable':True,'pause_blocks_offer':True,'resume_idempotent':True,'identity_and_config_preserved':True,'controller_zip_version':manifest['version'],'controller_zip_files':files,'controller_zip_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'all_manifest_hashes_match':True,'business_runs':0,'production_agent_processes':0,'two_physical_computers':False}
 (root/'real-upgrade-protocol.json').write_text(json.dumps(result,indent=2)+'\n','utf-8')
 print(json.dumps(result))
finally:
 if server:server.should_exit=True
 if thread:thread.join(10)
 case.doCleanups()
