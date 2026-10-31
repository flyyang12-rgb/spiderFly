import assert from 'node:assert/strict'
import { JSDOM } from '../test-runtime/node_modules/jsdom/lib/api.js'
import { File } from 'node:buffer'
const dom = new JSDOM('<!doctype html><body><button id="origin">新建</button></body>', { url: 'http://127.0.0.1:18763/' })
for (const key of ['window','document','Node','Element','HTMLElement','SVGElement','Document','ShadowRoot','MutationObserver']) globalThis[key] = dom.window[key]
globalThis.File = File
HTMLElement.prototype.getClientRects = function () { for (let p=this;p;p=p.parentElement) if (p.hidden || p.style?.display==='none') return []; return this.isConnected ? [{}] : [] }
HTMLElement.prototype.scrollIntoView = () => {}
const { createApp, h, reactive, ref, nextTick } = await import('vue')
const { default: PlanFields } = await import('./src/features/schedule/PlanFields.vue')
const { default: CreationDialog } = await import('./src/features/taskCenter/CreationDialog.vue')
const { default: WorkflowEditor } = await import('./src/features/workflows/WorkflowEditor.vue')
const { default: PlanEditor } = await import('./src/features/schedule/PlanEditor.vue')
const { useWorkflows } = await import('./src/features/workflows/useWorkflows.js')
const { createAppsState, useApps } = await import('./src/features/apps/useApps.js')
const { createTasksState, useTasks } = await import('./src/features/tasks/useTasks.js')
const { emptyPlan, readPlan, planBody } = await import('./src/lib/plans.js')
const { workspaceKey } = await import('./src/workspace/context.js')
const json = (value, status=200) => new Response(JSON.stringify(value), { status, headers: {'Content-Type':'application/json'} })
const calls = []
let route = () => json({})
globalThis.fetch = async (url, options={}) => { calls.push({url, ...options}); return url.includes('analyze-dependencies') ? json({ready:true,requirements:'',unresolved:[],items:[],target:{kind:'controller',version:'3.12.10'}}) : route(url,options) }
const flush = async () => { await nextTick(); await new Promise(setImmediate); await nextTick() }
const pause = async () => { await new Promise(resolve=>setTimeout(resolve,340)); await flush() }
let passed=0
async function check(name, fn) { calls.length=0; route=()=>json({}); await fn(); passed++; console.log('PASS '+name) }
function mount(Component, props, workspace={}) {
  const host=document.createElement('div'); document.body.append(host)
  const app=createApp({render:()=>h(Component,props)}); app.provide(workspaceKey, workspace); app.mount(host)
  return {app,host, close(){app.unmount();host.remove()}}
}
function click(root,text) { const button=[...root.querySelectorAll('button')].find(b=>b.textContent.trim()===text); assert(button, text); button.click() }
function change(input,value) { input.value=value;input.dispatchEvent(new window.Event(input.tagName==='SELECT'?'change':'input',{bubbles:true})) }
function preview(url) { return url.includes('preview') ? json({times:['2026-10-17T01:00:00Z','2026-10-18T01:00:00Z','2026-10-19T01:00:00Z','2026-10-20T01:00:00Z','2026-10-21T01:00:00Z']}) : null }

await check('shared fields keep weekly draft when switching manual and timed', async()=>{
  route=url=>preview(url)||json({})
  const form=reactive(emptyPlan()), t=mount(PlanFields,{form})
  assert.equal(form.trigger_type,'manual');click(t.host,'定时');await flush()
  change(t.host.querySelector('select'),'weekly');await flush()
  change(t.host.querySelector('input[type=time]'),'18:25');await flush()
  const before=[...form.weekly_days];click(t.host,'手动');await flush();assert.equal(form.trigger_type,'manual')
  click(t.host,'定时');await flush();assert.equal(form.trigger_type,'weekly');assert.equal(form.weekly_time,'18:25');assert.deepEqual([...form.weekly_days],before)
  await pause();assert.equal(t.host.querySelectorAll('li').length,5);assert.equal(t.host.querySelector('details').open,false)
  assert.equal(JSON.parse(calls.at(-1).body).trigger_config.time,'18:25');t.close()
})
await check('remote monthly is available and legacy plans stay read only',async()=>{
  const form=reactive({...emptyPlan(),trigger_type:'daily'}), t=mount(PlanFields,{form,remote:true})
  assert.equal(t.host.querySelector('option[value=cron]'),null)
  assert(t.host.querySelector('option[value=monthly]'))
  form.trigger_type='cron';await flush();assert.equal(t.host.querySelector('option[value=cron]').disabled,true);assert.match(t.host.textContent,/原计划保持不变/);t.close()
})
await check('preview failure is visible without opening collapsed future times',async()=>{
  route=()=>json({detail:'请选择有效时间'},400)
  const t=mount(PlanFields,{form:reactive({...emptyPlan(),trigger_type:'weekly',weekly_days:[]})})
  await pause();assert.match(t.host.querySelector('[role=alert]').textContent,/有效时间/);assert.equal(t.host.querySelector('details').open,false);t.close()
})
await check('older preview response cannot replace newer time',async()=>{
  const waiting=[];route=()=>new Promise(resolve=>waiting.push(resolve))
  const form=reactive({...emptyPlan(),trigger_type:'daily'}),t=mount(PlanFields,{form})
  await pause();form.daily_time='20:00';await pause();assert.equal(waiting.length,2)
  waiting[1](json({times:['2026-10-17T12:00:00Z']}));await flush();waiting[0](json({times:['2026-10-17T01:00:00Z']}));await flush()
  assert.match(t.host.querySelector('li').textContent,/20:00/);t.close()
})
function workspace() {
  const state=createAppsState(), me=ref({id:1}),isAdmin=ref(true), events=[]
  const api=useApps({...state,me,isAdmin,loadAll:async()=>{},showToast:(...a)=>events.push(a),handleSessionExpired:()=>{me.value=null}})
  Object.assign(state.appForm,{name:'合成任务',script:new File(['print(1)'],'a.py'),notify_on_success:false,notify_on_failure:false})
  return {...state,...api,me,isAdmin,events,createObjectOpen:ref(true),createObjectKind:ref('task'),targetHosts:ref([]),targetHostsLoading:ref(false),loadTargetHosts:async()=>{},handleSessionExpired:()=>{me.value=null},showToast:(...a)=>events.push(a)}
}
await check('task upload submits inline daily schedule and closes with no second plan action',async()=>{
  const w=workspace();route=(url,opt)=>url.endsWith('/apps')?json({task:{id:7,name:'合成任务',trigger_type:'daily'}}):preview(url)||json([])
  const t=mount(CreationDialog,{},w);await pause();click(t.host,'定时');await flush()
  change(t.host.querySelector('#unified-create-task input[type=time]'),'16:40');await flush()
  t.host.querySelector('#unified-create-task').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await flush()
  const posts=calls.filter(c=>c.url==='/api/apps');assert.equal(posts.length,1)
  assert.equal(posts[0].body.get('trigger_type'),'daily');assert.equal(JSON.parse(posts[0].body.get('trigger_config')).time,'16:40');assert.match(posts[0].body.get('request_id'),/^create-/)
  assert.equal(w.createObjectOpen.value,false);assert(![...t.host.querySelectorAll('button')].some(b=>b.textContent==='设置定时'));t.close()
})
await check('workflow creation uses one POST with members and entire workflow monthly plan',async()=>{
  const w=workspace();w.createObjectKind.value='workflow'
  route=(url,opt)=>url.endsWith('/workflows/tasks')?json([{id:1,name:'A',eligible:true},{id:2,name:'B',eligible:true}]):url.endsWith('/workflows')&&opt.method==='POST'?json({id:9,name:'AB'}):preview(url)||json([])
  const t=mount(CreationDialog,{},w);await pause();await flush();const form=t.host.querySelector('#unified-create-workflow')
  change(form.querySelector('input'),'流程AB');for(const id of ['1','2']) {change(form.querySelector('select'),id);await flush();click(form,'添加步骤');await flush()}
  click(form,'定时');await flush();change(form.querySelector('.plan-fields select'),'monthly');await flush()
  change(form.querySelectorAll('.plan-fields select')[1],'last');change(form.querySelector('input[type=time]'),'08:00');await flush();form.dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await flush()
  const posts=calls.filter(c=>c.method==='POST'&&c.url==='/api/workflows');assert.equal(posts.length,1)
  const body=JSON.parse(posts[0].body);assert.deepEqual(body.task_ids,[1,2]);assert.deepEqual(body.schedule,{trigger_type:'monthly',trigger_config:{day:'last',time:'08:00'},schedule_enabled:true});assert.equal(w.createObjectOpen.value,false);t.close()
})
await check('validation failure keeps name, file and inline time for correction',async()=>{
  const w=workspace();route=(url)=>url.endsWith('/apps')?json({detail:'名称已存在'},409):preview(url)||json([])
  const t=mount(CreationDialog,{},w);await pause();click(t.host,'定时');await flush();change(t.host.querySelector('#unified-create-task input[type=time]'),'17:20');await flush()
  t.host.querySelector('#unified-create-task').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await flush()
  assert.equal(w.createObjectOpen.value,true);assert.equal(w.appForm.daily_time,'17:20');assert.equal(w.appForm.script.name,'a.py');assert.match(t.host.textContent,/名称已存在/);t.close()
})
await check('lost task response is resolved by receipt without another upload',async()=>{
  const w=workspace();route=(url)=>{if(url.endsWith('/apps'))throw Error('lost response');if(url.includes('/creation-requests/task/'))return json({kind:'task',id:7,object:{id:7,name:'合成任务'}});return preview(url)||json([])}
  const t=mount(CreationDialog,{},w);await pause();t.host.querySelector('#unified-create-task').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await flush()
  assert.equal(w.uploadUncertain.value,true);click(t.host,'核对创建结果');await flush();assert.equal(w.createObjectOpen.value,false);assert.equal(calls.filter(c=>c.url==='/api/apps').length,1);t.close()
})
await check('explicit lost-response retry reuses the frozen upload and same request ID',async()=>{
  const w=workspace();let count=0;route=(url)=>{if(url.endsWith('/apps')){if(++count===1)throw Error('lost');return json({task:{id:8,name:'合成任务'}})}return preview(url)||json([])}
  await w.uploadApp();const first=calls[0].body;await w.uploadApp({retryUncertain:true});assert.equal(calls[1].body,first);assert.equal(calls[1].body.get('request_id'),first.get('request_id'));w.resetCreateSession()
})
await check('disabled saved workflow plan is not overwritten by definition-only edit',async()=>{
  const flow={id:4,name:'流程',description:'',revision:3,trigger_type:'cron',trigger_config:{expression:'0 8 * * *'},schedule_enabled:false,steps:[{task_id:1,task_name:'A'}]};let state
  const w={me:ref({id:1}),isAdmin:ref(true),handleSessionExpired:()=>{}}
  route=(url,opt)=>url.includes('/workflows/tasks')?json([{id:1,name:'A',eligible:true}]):opt.method==='PATCH'?json({...flow,name:'改名'}):json([flow])
  const Root={setup(){state=useWorkflows({isAdmin:w.isAdmin,me:w.me,onSessionExpired:w.handleSessionExpired,mode:'definitions'});return()=>state.editor.value?h(WorkflowEditor,{state}):h('div')}}
  const t=mount(Root,{},w);await flush();state.openEditor(flow);await flush();assert.equal(state.form.trigger_type,'manual');state.form.name='改名';await state.saveWorkflow();assert(!('schedule' in JSON.parse(calls.find(c=>c.method==='PATCH').body)));t.close()
})
await check('workflow full edit sends definition and changed plan in one revisioned PATCH',async()=>{
  const flow={id:4,name:'流程',description:'',revision:3,trigger_type:'daily',trigger_config:{time:'09:00'},schedule_enabled:true,steps:[{task_id:1,task_name:'A'}]};let state
  const w={me:ref({id:1}),isAdmin:ref(true),handleSessionExpired:()=>{}}
  route=(url,opt)=>url.includes('/workflows/tasks')?json([{id:1,name:'A',eligible:true}]):url.includes('preview')?preview(url):opt.method==='PATCH'?json({...flow,revision:4}):json([flow])
  const Root={setup(){state=useWorkflows({isAdmin:w.isAdmin,me:w.me,onSessionExpired:w.handleSessionExpired,mode:'definitions'});return()=>state.editor.value?h(WorkflowEditor,{state}):h('div')}}
  const t=mount(Root,{},w);await flush();state.openEditor(flow);await flush();state.form.name='新名称';state.form.daily_time='11:15';await state.saveWorkflow()
  const posts=calls.filter(c=>c.method==='PATCH');assert.equal(posts.length,1);const body=JSON.parse(posts[0].body);assert.equal(body.name,'新名称');assert.equal(body.revision,3);assert.deepEqual(body.schedule,{trigger_type:'daily',trigger_config:{time:'11:15'},schedule_enabled:true});t.close()
})
await check('single-task edit keeps disabled flag, version and time; conflict keeps input',async()=>{
  const s=createTasksState(), saving=ref(false),me=ref({id:1});const events=[]
  const state=useTasks({...s,saving,me,isAdmin:ref(true),taskIsActive:()=>false,loadAll:async()=>{},showToast:(...a)=>events.push(a)})
  route=(url,opt)=>opt.method==='PATCH'?json({detail:'任务已被其他伙伴修改，请刷新后重试'},409):json([])
  state.openEdit({id:1,name:'旧名',version:7,enabled:false,trigger_type:'weekly',trigger_config:{time:'09:30',weekdays:[1,3]},app_id:2});s.taskForm.name='保留输入';s.taskForm.weekly_time='19:30';await state.saveTask()
  const body=JSON.parse(calls.find(c=>c.method==='PATCH').body);assert.equal(body.enabled,false);assert.equal(body.version,7);assert.equal(body.trigger_config.time,'19:30');assert.equal(s.taskModalOpen.value,true);assert.equal(s.taskForm.name,'保留输入');state.resetTaskSession()
})
await check('plan shortcut refresh retains original edit revision and cannot bypass conflict',async()=>{
  let revision=4
  route=(url,opt)=>url==='/api/workflows'?json([{id:1,name:'流程',revision,trigger_type:'daily',trigger_config:{time:'09:00'},schedule_enabled:true,steps:[]}]):url.includes('preview')?preview(url):opt.method==='PATCH'?json({detail:'已被其他操作修改'},409):json([])
  const w={me:ref({id:1}),isAdmin:ref(true),handleSessionExpired:()=>{},loadAll:async()=>{},showToast:()=>{}}
  const t=mount(PlanEditor,{initialKind:'workflow',initialId:1,lockedObject:true},w);await flush();revision=5
  // The explicit refresh preserves the draft's original revision.
  const instance=t.app._instance.subTree.component.setupState
  instance.form.daily_time='16:00';await instance.refreshOptions();await instance.save();assert.equal(JSON.parse(calls.find(c=>c.method==='PATCH').body).revision,4);assert.equal(instance.form.daily_time,'16:00');t.close()
})
await check('receipt replay reports actual ready or failed environment instead of always preparing',async()=>{
  for(const environment_status of ['ready','failed']) {
    const w=workspace();route=()=>json({environment_status,task:{id:10,name:'已保存任务'}})
    await w.uploadApp();const last=w.events.at(-1);assert.equal(last[1],'任务已保存')
    assert.match(last[2],environment_status==='ready'?/已就绪/:/安装失败/);assert.equal(last[0],environment_status==='ready'?'success':'error');w.resetCreateSession()
  }
})
console.log(`${passed} inline plan DOM/behavior checks passed`)
dom.window.close()
