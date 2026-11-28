"""Exercise real local PY/ZIP timeouts and web feedback in an isolated service.

Requires backend development dependencies, Playwright, Microsoft Edge, Windows,
and a built frontend/dist. No user programs or production data are loaded.
"""
import os
for key in list(os.environ):
 if key.startswith(('SPIDERFLY_', 'FEISHU_', 'DEEPSEEK_')) or key in ('PYTHONPATH', 'PYTHONHOME'):
  os.environ.pop(key, None)
from pathlib import Path
import asyncio, ctypes, io, json, secrets, socket, sys, tempfile, threading, time, zipfile
from ctypes import wintypes
root=Path(__file__).resolve().parents[2]
stage=Path(tempfile.mkdtemp(prefix='spiderfly-timeout-web-'))
os.environ.update(SPIDERFLY_DATA_DIR=str(stage/'data'), SPIDERFLY_WORK_DIR=str(stage/'work'), SPIDERFLY_TASK_TIMEOUT_SECONDS='60')
sys.path.insert(0, str(root/'backend'))
from app import config, database, security, maintenance, environments, native_process, runner
from app.services import execution_queue, host_dispatch
from app.main import app
import uvicorn
from playwright.sync_api import sync_playwright, expect
assert os.name=='nt', 'Windows process handles are required for this check'
assert config.PROJECT_ROOT==root and config.DEFAULT_TASK_TIMEOUT_SECONDS==60
assert all(p.is_relative_to(stage) for p in (config.DATA_DIR,config.WORK_DIR,config.RPA_APPS_DIR,config.RPA_ENVS_DIR,config.EXECUTIONS_DIR))
database.init_db(); maintenance.init_tables(); host_dispatch.init_tables()
password=secrets.token_urlsafe(20); now=database.utc_now()
database.execute("INSERT INTO users(username,display_name,password_hash,role,active,must_change_password,created_at,updated_at) VALUES('timeout-qa','QA',?,'super_admin',1,0,?,?)",(security._hash_password(password),now,now))
sock=socket.socket(); sock.bind(('127.0.0.1',0))
url='http://127.0.0.1:'+str(sock.getsockname()[1])
server=uvicorn.Server(uvicorn.Config(app,lifespan='off',log_level='error'))
loop_box={}
async def serve():
 loop_box['loop']=asyncio.get_running_loop()
 await server.serve(sockets=[sock])
thread=threading.Thread(target=lambda:asyncio.run(serve()),daemon=True); thread.start()
kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];kernel.WaitForSingleObject.restype=wintypes.DWORD
kernel.CloseHandle.argtypes=[wintypes.HANDLE]
handles=[]
worker=None
# Both children inherit stdout; timeouts must close the entire process tree.
grand="import os,time\nprint('PIDS '+str([os.getpid()]),flush=True)\ntime.sleep(120)\n"
child="import subprocess,sys,time,os,json\np=subprocess.Popen([sys.executable,'-u','-c',"+repr(grand)+"])\nprint('PIDS '+json.dumps([os.getpid()]),flush=True)\ntime.sleep(120)\n"
slow="import subprocess,sys,time,os,json\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-u','-c',"+repr(child)+"])\nprint('PIDS '+json.dumps([os.getpid()]),flush=True)\nPath(os.environ['SPIDERFLY_WORK_DIR'],'temporary.txt').write_text('synthetic')\ntime.sleep(120)\n"
async def drain():
 # Only the copied runner and copied DB are used, without a second production
 # scheduler or any AI/notification worker.
 while True:
  execution=await asyncio.to_thread(execution_queue._claim_next_execution)
  if execution is None:
   await asyncio.sleep(.1);continue
  await runner.run_execution(execution)
try:
 for _ in range(100):
  if server.started:break
  time.sleep(.05)
 assert server.started
 with sync_playwright() as pw:
  browser=pw.chromium.launch(channel='msedge',headless=True)
  page=browser.new_page(viewport={'width':1440,'height':960});page.set_default_timeout(10000)
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  assert page.request.post(url+'/api/auth/login',data={'username':'timeout-qa','password':password}).ok
  def upload(kind,name,source):
   fields={'name':name,'notify_on_success':'false','notify_on_failure':'false','failure_screenshot':'false'}
   if kind=='zip':
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as archive:
     archive.writestr('ordinary/run.py',source)
     archive.writestr('ordinary/requirements.txt','# standard library only\n')
    fields.update(project_entry='ordinary/run.py',project_archive={'name':'ordinary.zip','mimeType':'application/zip','buffer':buf.getvalue()})
   else: fields['script']={'name':'ordinary.py','mimeType':'text/x-python','buffer':source.encode()}
   response=page.request.post(url+'/api/apps',multipart=fields)
   assert response.status==201,(response.status,response.text())
   created=response.json()
   asyncio.run_coroutine_threadsafe(environments.build_environment(created['id']),loop_box['loop']).result(timeout=90)
   state=database.fetch_one('SELECT environment_status,environment_error FROM rpa_apps WHERE id=?',(created['id'],))
   assert state['environment_status']=='ready',state
   return created['task']['id']
  py_id=upload('py','普通 PY 超时检查',slow)
  zip_id=upload('zip','普通 ZIP 超时检查',slow)
  follow_id=upload('py','后续正常任务',"print('FOLLOWUP_OK',flush=True)\n")
  worker=asyncio.run_coroutine_threadsafe(drain(),loop_box['loop'])
  page.goto(url)
  def start(name):
   if page.get_by_role('dialog').count():
    page.keyboard.press('Escape');expect(page.get_by_role('dialog')).to_have_count(0)
   page.get_by_role('button',name='任务中心',exact=True).click()
   with page.expect_response(lambda r:'/api/tasks/' in r.url and r.url.endswith('/run') and r.request.method=='POST') as receipt:
    page.locator('tbody tr').filter(has_text=name).get_by_role('button',name='运行',exact=True).click()
   result=receipt.value.json();assert result['run_type']=='local'
   expect(page.get_by_role('dialog',name='执行日志',exact=True)).to_be_visible()
   return result['execution_id']
  for kind,task_id,name in [('py',py_id,'普通 PY 超时检查'),('zip',zip_id,'普通 ZIP 超时检查')]:
   began=time.monotonic();execution=start(name)
   pids=set();deadline=time.monotonic()+12
   while len(pids)<3 and time.monotonic()<deadline:
    detail=page.request.get(f'{url}/api/executions/{execution}').json()
    for line in detail['stdout'].splitlines():
     if line.startswith('PIDS '):pids.update(json.loads(line[5:]))
    page.wait_for_timeout(150)
   assert len(pids)==3,(kind,detail)
   current_handles=[]
   for pid in pids:
    handle=kernel.OpenProcess(0x100000|0x1000,False,pid)
    assert handle,(pid,ctypes.get_last_error())
    handles.append(handle);current_handles.append(handle)
    assert kernel.WaitForSingleObject(handle,0)==258
   follow=start('后续正常任务')
   pending=page.request.get(f'{url}/api/executions/{follow}').json()
   assert pending['status']=='pending',pending
   print(json.dumps({'phase':'running','kind':kind,'execution_id':execution,'owned_processes':len(pids),'next_status':pending['status']},ensure_ascii=False),flush=True)
   page.keyboard.press('Escape');page.get_by_role('button',name='工作台',exact=True).click()
   toast=page.locator('.toast')
   expect(toast.locator('strong')).to_have_text('运行超时',timeout=80000)
   expect(toast).to_contain_text('#'+str(execution))
   detail=page.request.get(f'{url}/api/executions/{execution}').json()
   assert detail['status']=='timeout' and detail['ended_at']
   assert detail['exit_code'] is not None and detail['exit_code']!=0,detail
   assert '60' in detail['error_message'] and '已终止' in detail['error_message']
   assert all(kernel.WaitForSingleObject(h,0)==0 for h in current_handles),'A timeout left an owned process alive'
   assert not list(config.WORK_DIR.iterdir()),'Workspace was not cleared'
   if kind=='zip':
    with database.connection() as conn:
     assert native_process.cleanup_confirmed(conn,execution)
   page.screenshot(path=str(stage/f'{kind}-timeout.png'),animations='disabled')
   toast.get_by_role('button',name='查看日志',exact=True).click()
   drawer=page.get_by_role('dialog',name='执行日志',exact=True)
   expect(drawer).to_contain_text('超时');expect(drawer).to_contain_text('已终止')
   expect(toast.locator('strong')).to_have_text('运行成功')
   expect(toast).to_contain_text('#'+str(follow))
   after=page.request.get(f'{url}/api/executions/{follow}').json()
   assert after['status']=='success' and 'FOLLOWUP_OK' in after['stdout'],after
   toast.get_by_role('button',name='关闭通知',exact=True).click()
   print(json.dumps({'phase':'complete','kind':kind,'status':detail['status'],'elapsed_seconds':round(time.monotonic()-began,2),'all_three_processes_exited':True,'workspace_empty':True,'next_task_status':after['status'],'web_notice':'运行超时','logs_opened':True},ensure_ascii=False),flush=True)
  assert not errors,errors
  browser.close()
  print('PASS: real PY and ZIP 60-second timeouts; parent, child and grandchild exit; queue continuation; terminal DB state; web notice and logs.',flush=True)
  print('Evidence: '+str(stage),flush=True)
finally:
 if worker:worker.cancel()
 for handle in handles:kernel.CloseHandle(handle)
 server.should_exit=True;thread.join(8);sock.close()
