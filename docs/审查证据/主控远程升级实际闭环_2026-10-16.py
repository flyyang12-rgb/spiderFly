"""Copy to a fresh isolated root. Real HTTP/Client/PowerShell; synthetic lock and launch script."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,sys,threading,time,uuid,venv
root=Path(__file__).resolve().parent
assert root.name.startswith('spiderfly-check-') and not(root/'.env').exists()
for key in tuple(os.environ):
 if key.upper().startswith(('SPIDERFLY_','FEISHU_','LARK_','DEEPSEEK_','OPENAI_')) or key.upper() in ('PYTHONPATH','PYTHONHOME','PSMODULEPATH'):os.environ.pop(key)
sys.path[:0]=[str(root/'backend'),str(root/'agent')]
from app import config,database,main,security
assert config.PROJECT_ROOT==root
for name in ('DATA_DIR','RPA_APPS_DIR','RPA_ENVS_DIR','EXECUTIONS_DIR','WORK_DIR'):assert getattr(config,name).resolve().is_relative_to(root)
from tests.test_hosts import HostApiTests
from app.api import hosts as host_api
from app.services import host_dispatch,agent_updates
from spiderfly_agent.identity import load_identity,save_json
from unittest.mock import patch
import uvicorn

case=HostApiTests();case.setUp();agents=[];server=None;thread=None;proof=[]
try:
 database.execute("UPDATE users SET role='super_admin',password_hash=? WHERE username='admin'",(security._hash_password('Synthetic-Upgrade-Only-2026!'),))
 future=case.root/'Future package';shutil.copytree(root/'agent',future,ignore=shutil.ignore_patterns('tests','__pycache__','.venv'))
 (future/'spiderfly_agent/__init__.py').write_text('__version__ = "0.5.1"\nPROTOCOL_VERSION = 1\n','utf-8')
 start='''param([string]$DataDir,[string]$Server,[string]$Code,[string]$Name,[switch]$Dispatch)
$argsAgent=@('-X','utf8','-m','spiderfly_agent','--data-dir',$DataDir,'run')
if($Dispatch){$argsAgent+='--dispatch'}
& (Join-Path $PSScriptRoot '.venv/Scripts/python.exe') @argsAgent
exit $LASTEXITCODE
'''
 (future/'start.ps1').write_text(start,'utf-8-sig')
 case.stack.enter_context(patch.object(host_api,'AGENT_ROOT',future));case.stack.enter_context(patch.object(host_dispatch,'CURRENT_AGENT_VERSION','0.5.1'))
 server=uvicorn.Server(uvicorn.Config(main.app,host='127.0.0.1',port=18763,lifespan='off',log_level='error'))
 thread=threading.Thread(target=server.run,daemon=True);thread.start()
 deadline=time.monotonic()+10
 while not server.started and time.monotonic()<deadline:time.sleep(.05)
 assert server.started

 def ps(install,data,action,environment):
  out=install.parent/'manager.out';err=install.parent/'manager.err'
  with out.open('wb') as stdout,err.open('wb') as stderr:
   result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(install/'manage.ps1'),action,'-DataDir',str(data),*(['-Dispatch'] if action=='Start' else [])],cwd=install,env=environment,stdout=stdout,stderr=stderr,timeout=40,creationflags=subprocess.CREATE_NO_WINDOW)
  details=(data/'logs/agent.error.log').read_text('utf-8',errors='replace') if (data/'logs/agent.error.log').exists() else ''
  if result.returncode and (data/'agent.pid.json').exists():
   record=json.loads((data/'agent.pid.json').read_text('utf-8'))
   command=f"[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); Get-CimInstance Win32_Process -Filter 'ProcessId={record['pid']}' | Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json"
   p=subprocess.run(['powershell.exe','-NoProfile','-Command',command],capture_output=True,timeout=10,env=environment)
   details+='\n'+p.stdout.decode('utf-8','replace')+'\n'+(install/'.venv/pyvenv.cfg').read_text('utf-8')
  assert result.returncode==0,(action,err.read_text('utf-8',errors='replace'),details)

 def wait(predicate,timeout=90):
  deadline=time.monotonic()+timeout
  while time.monotonic()<deadline:
   value=predicate()
   if value:return value
   time.sleep(.15)
  raise AssertionError('Synthetic remote upgrade timed out')

 def agent(label):
  base=case.root/label;base.mkdir();(base/'.synthetic-only').touch()
  install=base/'Agent';shutil.copytree(root/'agent',install,ignore=shutil.ignore_patterns('tests','__pycache__','.venv'))
  (install/'start.ps1').write_text(start,'utf-8-sig')
  save_json(install/'controller-trust.json',agent_updates.trust(agent_updates.signing_key()))
  venv.EnvBuilder(with_pip=False,system_site_packages=True).create(install/'.venv')
  site=install/'.venv/Lib/site-packages';site.mkdir(parents=True,exist_ok=True)
  (site/'fixture-dependencies.pth').write_text(str(Path(sys.executable).parents[1]/'Lib/site-packages')+'\n','utf-8')
  hooks=base/'Hooks';hooks.mkdir();guard=repr(str(base))
  hooks_code=f'''from pathlib import Path
import os,sys
fixture=Path({guard});assert (fixture/'.synthetic-only').is_file()
assert fixture.is_relative_to(Path(__import__('tempfile').gettempdir()).resolve())
from spiderfly_agent import windows
real=windows.SingleInstance
windows.SingleInstance=lambda directory:real(directory,key='isolated-remote-upgrade-'+fixture.name+'-{uuid.uuid4().hex}')
windows.interactive_session=lambda:True
from spiderfly_agent.api import Api
original=Api.request
def request(self,method,path,value=None,**kw):
 result=original(self,method,path,value,**kw)
 if path.endswith('/report') and value and value.get('phase')=='result' and not(fixture/'lost-result-once').exists():
  (fixture/'lost-result-once').touch()
  raise __import__('requests').ConnectionError('synthetic response lost after commit')
 return result
Api.request=request
'''
  (hooks/'sitecustomize.py').write_text(hooks_code,'utf-8')
  data=base/'State';data.mkdir();machine,key=load_identity(data)
  payload,_=case.registration(key=key,machine_id=machine);payload['agent_version']='0.5.0';payload['name']=label
  registered=case.success(case.request('POST','/api/agent/register',payload,user=None));host=registered['host_id'];case.keys[host]=key
  case.success(case.request('POST',f'/api/hosts/{host}/approve'));case.success(case.request('PATCH',f'/api/hosts/{host}/mode',{'dispatch_enabled':True}))
  save_json(data/'config.json',{'server':'http://127.0.0.1:18763','host_id':host,'name':label})
  before={n:(data/n).read_bytes() for n in ('identity.json','config.json')}
  environment={k:v for k,v in os.environ.items() if k.upper()!='PSMODULEPATH'}
  environment['PYTHONPATH']=str(hooks)+os.pathsep+str(install);environment['LOCALAPPDATA']=str(base/'Local')
  item={'host':host,'install':install,'data':data,'environment':environment,'before':before,'base':base};agents.append(item)
  ps(install,data,'Start',environment)
  wait(lambda:case.host(host)['remote_upgrade_supported'])
  return item

 def run(item,expected):
  host=item['host'];job=case.success(case.request('POST',f'/api/hosts/{host}/upgrade',{'request_id':uuid.uuid4().hex}))
  seen=[]
  def complete():
   value=case.host(host);status=(value['agent_update'] or {}).get('status')
   if status and status not in seen:seen.append(status)
   return value if status in {'succeeded','failed','uncertain'} else None
  final=wait(complete)
  if final['agent_update']['status']!=expected:
   raise AssertionError(final['agent_update'])
  version='0.5.1' if expected=='succeeded' else '0.5.0'
  assert final['agent_version']==version and not final['upgrade_pending']
  assert final['dispatch_enabled'] and final['agent_dispatch_enabled']
  for name,content in item['before'].items():assert (item['data']/name).read_bytes()==content
  assert (item['base']/'lost-result-once').exists()
  assert database.fetch_one('SELECT COUNT(*) n FROM agent_update_jobs WHERE host_id=?',(host,))['n']==1
  jobroot=item['base']/'Local/SpiderFlyAgent-upgrades'/('remote-'+job['id'])
  assert (jobroot/'state-before.zip').is_file() and (jobroot/'program-before.zip').is_file()
  proof.append({'case':expected,'states_observed':seen,'same_host_id':True,'identity_config_preserved':True,'dispatch_modes_preserved':True,'result_response_lost_then_confirmed':True,'single_upgrade_job':True,'version':version,'private_state_and_program_backups':True})
  ps(item['install'],item['data'],'Stop',item['environment'])

 first=agent('自动升级成功');run(first,'succeeded')
 (future/'start.ps1').write_text('exit 9\n','utf-8-sig')
 second=agent('启动失败恢复旧版');run(second,'failed')
 (future/'start.ps1').write_text(start,'utf-8-sig')
 third=agent('页面验收电脑')
 save_json(root/'ui-credentials.json',{'username':'admin','password':'Synthetic-Upgrade-Only-2026!'})
 save_json(root/'remote-ui-ready.json',{'host_id':third['host'],'source':'synthetic fixture only','server':'http://127.0.0.1:18763'})
 (root/'remote-ui-ready').touch()
 print('REAL SUCCESS AND ROLLBACK PASSED; isolated browser server ready on 18763',flush=True)
 deadline=time.monotonic()+1200
 while not(root/'stop-remote-ui').exists() and time.monotonic()<deadline:time.sleep(.25)
 browser=case.host(third['host'])
 result={'cases':proof,'browser_host_version':browser['agent_version'],'browser_host_update':browser['agent_update'],
         'business_runs':database.fetch_one('SELECT COUNT(*) n FROM remote_runs')['n'],
         'real_client_updater_http_and_powershell':True,'fixture_start_script':True,'fixture_lock_only':True,
         'production_agent_processes':0,'two_physical_computers':False}
 save_json(root/'real-remote-upgrade.json',result)
 print(json.dumps(result,ensure_ascii=False),flush=True)
finally:
 for item in agents:
  if (item['data']/'agent.pid.json').exists():
   try:ps(item['install'],item['data'],'Stop',item['environment'])
   except Exception:
    (item['data']/'shutdown.request').write_text('synthetic cleanup')
    for _ in range(50):
     if not(item['data']/'agent.pid.json').exists():break
     time.sleep(.1)
 if server:server.should_exit=True
 if thread:thread.join(10)
 case.doCleanups()
