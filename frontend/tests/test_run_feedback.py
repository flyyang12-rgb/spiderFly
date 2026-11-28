"""Isolated browser regression for manual-run completion feedback.

Run with a Python environment containing backend development dependencies and
Playwright, after building frontend/dist. Requires Microsoft Edge on Windows.
No production settings, business programs or external services are used.
"""
import os
for k in list(os.environ):
 if k.startswith(('SPIDERFLY_','FEISHU_','DEEPSEEK_')) or k in ('PYTHONPATH','PYTHONHOME'):os.environ.pop(k,None)
from pathlib import Path
import os,sys,socket,time,threading,io,zipfile,secrets
from contextlib import closing
import tempfile
root=Path(__file__).resolve().parents[2]
stage=Path(tempfile.mkdtemp(prefix='spiderfly-run-feedback-'))
case=stage/('ui-'+secrets.token_hex(3))
os.environ['SPIDERFLY_DATA_DIR']=str(case/'data')
os.environ['SPIDERFLY_WORK_DIR']=str(case/'work')
sys.path.insert(0,str(root/'backend'))
from app import config,database,security,maintenance
from app.services import host_dispatch
from app.main import app
import uvicorn
from playwright.sync_api import sync_playwright
assert config.PROJECT_ROOT==root and all(p.is_relative_to(case) for p in (config.DATA_DIR,config.WORK_DIR,config.RPA_APPS_DIR,config.RPA_ENVS_DIR))
database.init_db(); host_dispatch.init_tables(); maintenance.init_tables()
password=secrets.token_urlsafe(20)
now=database.utc_now()
database.execute("INSERT INTO users(username,display_name,password_hash,role,active,must_change_password,created_at,updated_at) VALUES ('release-qa','QA',?,'super_admin',1,0,?,?)",(security._hash_password(password),now,now))
sock=socket.socket();sock.bind(('127.0.0.1',0))
url='http://127.0.0.1:'+str(sock.getsockname()[1])
server=uvicorn.Server(uvicorn.Config(app,lifespan='off',log_level='error'))
t=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);t.start()
try:
 for i in range(100):
  if server.started: break
  time.sleep(.04)

 with sync_playwright() as pw:
  from playwright.sync_api import expect
  browser=pw.chromium.launch(channel='msedge',headless=True)
  page=browser.new_page(viewport={'width':1440,'height':960})
  page.set_default_timeout(8000)
  assert page.request.post(url+'/api/auth/login',data={'username':'release-qa','password':password}).ok
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  tasks=[dict(id=101,name='离线合成任务',script_filename='probe.py',enabled=True,trigger_type='manual',trigger_config={},created_by=1,environment_status='ready',first_environment_ready=1)]
  rows={}
  current={'id':901,'kind':'local','status':'running','errors':0}
  attempts={}
  page.route('**/api/tasks',lambda r:r.fulfill(json=tasks))
  page.route('**/api/executions?limit=100',lambda r:r.fulfill(json=[]))
  page.route('**/api/executions/history*',lambda r:r.fulfill(json={'items':[],'total':0,'page':1,'page_size':20}))
  def detail(route):
   id=int(route.request.url.split('/')[-1])
   attempts[id]=attempts.get(id,0)+1
   if current['errors'] and id==current['id']:
    current['errors']-=1;route.fulfill(status=503,json={'detail':'synthetic transient failure'});return
   status=rows.get(id,'running')
   route.fulfill(json=dict(id=id,task_id=101,task_name=tasks[0]['name'],status=status,duration_ms=1234,created_at=now,started_at=now,ended_at=now,stdout='offline synthetic log',stderr='',artifacts={'files':[]},notification_status='disabled',finished_at=now,events=[{'seq':1,'kind':'stdout','text':'offline synthetic log','created_at':now}]))
  import re
  page.route(re.compile(r'/api/executions/\d+$'),detail)
  page.route(re.compile(r'/api/remote-runs/\d+$'),detail)
  def submit(route):
   if current['kind']=='remote':route.fulfill(json={'run_type':'remote','remote_run_id':current['id'],'target_host_name':'离线电脑'})
   else:route.fulfill(json={'execution_id':current['id']})
  page.route('**/api/tasks/101/run',submit)
  page.goto(url)
  expect(page.get_by_role('button',name='工作台',exact=True)).to_be_visible()
  # Old completed history never creates a completion notice.
  assert page.locator('.toast-action').count()==0
  def start(id,status=None,kind='local'):
   current.update(id=id,kind=kind,errors=0)
   rows[id]=status or 'running'
   if page.get_by_role('dialog').count():
    page.keyboard.press('Escape');expect(page.get_by_role('dialog')).to_have_count(0)
   page.get_by_role('button',name='任务中心',exact=True).click()
   page.get_by_role('button',name='运行',exact=True).click()
   expect(page.get_by_role('dialog',name='执行日志',exact=True) if kind=='local' else page.get_by_role('dialog')).to_be_visible()
  start(901)
  expect(page.get_by_role('dialog',name='执行日志',exact=True)).to_be_visible()
  page.keyboard.press('Escape')
  page.get_by_role('button',name='任务中心',exact=True).click()
  rows[901]='success'
  toast=page.locator('.toast')
  expect(toast.locator('strong')).to_have_text('运行成功')
  expect(toast).to_contain_text('#901')
  page.wait_for_timeout(4200)
  expect(toast).to_be_visible()
  page.keyboard.press('Escape');expect(toast).to_be_visible()
  page.screenshot(path=str(stage/'run-feedback-success.png'),animations='disabled')
  toast.get_by_role('button',name='查看日志',exact=True).click()
  expect(page.get_by_role('dialog',name='执行日志',exact=True)).to_contain_text('offline synthetic log')
  expect(page.locator('.toast-action')).to_have_count(0)
  # Very fast failure, completed before the first poll.
  start(902,'failed')
  expect(toast.locator('strong')).to_have_text('运行失败')
  # The notification remains usable above the inert drawer.
  toast.get_by_role('button',name='关闭通知',exact=True).click()
  expect(toast).to_have_count(0)
  start(903)
  page.keyboard.press('Escape')
  page.reload()
  expect(page.get_by_role('button',name='工作台',exact=True)).to_be_visible()
  rows[903]='timeout'
  expect(toast.locator('strong')).to_have_text('运行超时')
  # Unread completion survives reload and is removed only after acknowledging.
  page.reload()
  expect(toast.locator('strong')).to_have_text('运行超时')
  toast.get_by_role('button',name='关闭通知',exact=True).click()
  page.reload()
  expect(page.get_by_role('button',name='工作台',exact=True)).to_be_visible()
  page.wait_for_timeout(2300)
  expect(toast).to_have_count(0)
  start(904,'cancelled')
  expect(toast.locator('strong')).to_have_text('运行已停止')
  toast.get_by_role('button',name='关闭通知',exact=True).click()
  start(905,kind='remote')
  rows[905]='uncertain'
  page.wait_for_timeout(2600)
  assert page.locator('.toast-action').count()==0
  current['errors']=1
  rows[905]='succeeded'
  expect(toast.locator('strong')).to_have_text('运行成功',timeout=9000)
  expect(toast).to_contain_text('#905')
  toast.get_by_role('button',name='查看日志',exact=True).click()
  expect(page.get_by_role('dialog').last).to_contain_text('offline synthetic log')
  page.keyboard.press('Escape')
  # Two simultaneous completions are queued; neither is lost.
  start(906)
  page.keyboard.press('Escape')
  start(907)
  page.keyboard.press('Escape')
  rows[906]='failed';rows[907]='success'
  expect(toast.locator('strong')).to_have_text('运行失败')
  expect(toast).to_contain_text('还有 1 条提示')
  toast.get_by_role('button',name='关闭通知',exact=True).click()
  expect(toast.locator('strong')).to_have_text('运行成功')
  page.set_viewport_size({'width':390,'height':844})
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  assert toast.evaluate('e=>e.getBoundingClientRect().left>=0 && e.getBoundingClientRect().right<=innerWidth')
  page.screenshot(path=str(stage/'run-feedback-mobile.png'),animations='disabled')
  toast.get_by_role('button',name='关闭通知',exact=True).click()
  # Logout discards receipts and any late response, avoiding cross-account feedback.
  page.set_viewport_size({'width':1440,'height':960})
  start(908)
  page.keyboard.press('Escape')
  page.get_by_role('button',name='退出登录',exact=True).click()
  expect(page.get_by_role('button',name='登录',exact=True)).to_be_visible()
  rows[908]='success'
  page.wait_for_timeout(2300)
  assert page.locator('.toast-action').count()==0
  assert not page.evaluate("Object.keys(sessionStorage).some(k=>k.startsWith('spiderfly.run-feedback.'))")
  assert not errors,errors
  print('PASS: completion states; fast finish; navigation; refresh and unread persistence; sticky notice; logs; remote uncertain/retry; queue; mobile; logout.')
  browser.close()
finally:
 server.should_exit=True;t.join(5);sock.close()
