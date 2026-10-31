// Copy into an isolated frontend alongside check-loader.mjs; uses the external test-runtime jsdom.
import assert from 'node:assert/strict'
import { File } from 'node:buffer'
import { JSDOM } from '../test-runtime/node_modules/jsdom/lib/api.js'
const dom = new JSDOM('<!doctype html><body></body>', {url:'http://127.0.0.1:18763/'})
for (const key of ['window','document','Node','Element','HTMLElement','SVGElement','Document','ShadowRoot','MutationObserver']) globalThis[key] = dom.window[key]
globalThis.File = File
HTMLElement.prototype.getClientRects = function(){return this.isConnected ? [{}] : []}
HTMLElement.prototype.scrollIntoView = () => {}
const {createApp,h,reactive,ref,nextTick} = await import('vue')
const {default:PlanFields} = await import('./src/features/schedule/PlanFields.vue')
const {default:PlanEditor} = await import('./src/features/schedule/PlanEditor.vue')
const {emptyPlan,readPlan,planBody,validatePlan} = await import('./src/lib/plans.js')
const {triggerDetail} = await import('./src/lib/format.js')
const {createAppsState,useApps} = await import('./src/features/apps/useApps.js')
const {createTasksState,useTasks} = await import('./src/features/tasks/useTasks.js')
const {useSchedule} = await import('./src/features/schedule/useSchedule.js')
const {workspaceKey} = await import('./src/workspace/context.js')
const calls=[]
const json=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}})
let route=()=>json({})
globalThis.fetch=async(url,options={})=>{calls.push({url,...options});return route(url,options)}
const flush=async()=>{await nextTick();await new Promise(setImmediate);await nextTick()}
const pause=async()=>{await new Promise(resolve=>setTimeout(resolve,260));await flush()}
const change=(input,value)=>{input.value=value;input.dispatchEvent(new window.Event(input.tagName==='SELECT'?'change':'input',{bubbles:true}))}
const click=(root,text)=>{const item=[...root.querySelectorAll('button')].find(b=>b.textContent.trim()===text);assert(item,text);item.click()}
function mount(Component,props={},context={}){const host=document.createElement('div');document.body.append(host);const app=createApp({render:()=>h(Component,props)});app.provide(workspaceKey,context);app.mount(host);return{host,close(){app.unmount();host.remove()}}}
let passed=0
async function check(name,run){calls.length=0;route=()=>json({times:['2027-03-31T01:00:00+00:00']});await run();passed++;console.log('PASS '+name)}

await check('controller and Agent offer exactly daily weekly monthly, with real monthly fields',async()=>{
  for(const remote of [false,true]){
    const form=reactive({...emptyPlan(),trigger_type:'daily'}),t=mount(PlanFields,{form,remote})
    assert.deepEqual([...t.host.querySelector('select').options].map(o=>o.textContent),['每日','每周','每月'])
    change(t.host.querySelector('select'),'monthly');await flush()
    const date=t.host.querySelectorAll('select')[1];assert.equal(date.options.length,31)
    change(date,'31');change(t.host.querySelector('input[type=time]'),'18:25');await pause()
    assert.match(t.host.textContent,/当月没有该日期时跳过/)
    assert.equal(form.monthly_day,31)
    assert.deepEqual(JSON.parse(calls.at(-1).body),{trigger_type:'monthly',trigger_config:{day:31,time:'18:25'}})
    assert.equal(t.host.querySelector('details').open,false);assert.equal(t.host.querySelectorAll('li').length,1)
    click(t.host,'手动');await flush();click(t.host,'定时');await flush()
    assert.equal(form.trigger_type,'monthly');assert.equal(form.monthly_day,31);assert.equal(form.monthly_time,'18:25');t.close()
  }
})

await check('monthly read and save retain dates, time, disabled workflow plan and labels',async()=>{
  const task={trigger_type:'monthly',trigger_config:{day:29,time:'07:15'}}
  assert.deepEqual(planBody(readPlan(task)),{trigger_type:'monthly',trigger_config:{day:29,time:'07:15'}})
  assert.equal(triggerDetail(task),'每月 29 号 07:15')
  const form=readPlan({...task,schedule_enabled:false},true)
  assert.equal(form.trigger_type,'manual');assert.equal(form.saved_trigger_type,'monthly');assert.equal(form.monthly_day,29)
  for(const day of [0,32,1.5,NaN,'invalid']) assert.match(validatePlan({...emptyPlan(),trigger_type:'monthly',monthly_day:day}),/执行日期/)
  assert.equal(validatePlan({...emptyPlan(),trigger_type:'monthly',monthly_day:31},true),'')
})

await check('upload freezes a monthly plan and the selected Agent in one request',async()=>{
  const state=createAppsState(),me=ref({id:1}),isAdmin=ref(true)
  const api=useApps({...state,me,isAdmin,loadAll:async()=>{},showToast:()=>{}})
  Object.assign(state.appForm,{name:'合成月任务',script:new File(['print(1)'],'monthly.py'),target_host_id:'7',trigger_type:'monthly',monthly_day:31,monthly_time:'08:05'})
  route=(url)=>url==='/api/apps'?json({environment_status:'not_built',task:{id:9,target_host_id:7}}):json({})
  await api.uploadApp()
  const posts=calls.filter(c=>c.url==='/api/apps');assert.equal(posts.length,1)
  assert.equal(posts[0].body.get('trigger_type'),'monthly');assert.equal(posts[0].body.get('target_host_id'),'7')
  assert.deepEqual(JSON.parse(posts[0].body.get('trigger_config')),{day:31,time:'08:05'})
  api.resetCreateSession()
})

await check('editing a monthly task retains or updates its saved day and time',async()=>{
  const state=createTasksState(),me=ref({id:1}),api=useTasks({...state,me,saving:ref(false),taskIsActive:()=>false,isAdmin:ref(true),loadAll:async()=>{},showToast:()=>{}})
  route=(url)=>url==='/api/hosts'?json([]):json({})
  api.resetTaskSession()
  api.openEdit({id:9,version:4,name:'合成月任务',enabled:true,trigger_type:'monthly',trigger_config:{day:31,time:'18:25'}});await flush()
  assert.equal(state.taskForm.monthly_day,31);assert.equal(state.taskForm.monthly_time,'18:25')
  const editor=mount(PlanFields,{form:state.taskForm})
  click(editor.host,'手动');await flush();click(editor.host,'定时');await flush()
  assert.equal(state.taskForm.trigger_type,'monthly');assert.equal(state.taskForm.monthly_day,31)
  editor.close()
  state.taskForm.monthly_day=15;state.taskForm.monthly_time='08:05';await api.saveTask()
  const body=JSON.parse(calls.find(c=>c.method==='PATCH').body)
  assert.equal(body.version,4);assert.equal(body.trigger_type,'monthly');assert.deepEqual(body.trigger_config,{day:15,time:'08:05'})
  api.resetTaskSession()
})

await check('monthly workflow can be enabled from a previously disabled saved plan',async()=>{
  const flow={id:5,name:'合成月流程',revision:8,trigger_type:'monthly',trigger_config:{day:31,time:'18:25'},schedule_enabled:false,steps:[]}
  route=(url,opt)=>url==='/api/workflows'?json([flow]):json({times:['2027-03-31T10:25:00Z']})
  const t=mount(PlanEditor,{initialKind:'workflow',initialId:5,lockedObject:true},{me:ref({id:1}),isAdmin:ref(true),loadAll:async()=>{},showToast:()=>{},handleSessionExpired:()=>{}})
  await flush();await flush();click(t.host,'定时');await flush()
  assert.equal(t.host.querySelector('select').value,'monthly');assert.equal(t.host.querySelectorAll('select')[1].value,'31')
  t.host.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await flush()
  const body=JSON.parse(calls.find(c=>c.method==='PATCH').body)
  assert.deepEqual(body,{revision:8,trigger_type:'monthly',trigger_config:{day:31,time:'18:25'},schedule_enabled:true});t.close()
})

await check('monthly timetable matches the selected calendar date instead of every day',async()=>{
  let schedule
  const tasks=ref([{id:1,name:'合成月任务',enabled:true,trigger_type:'monthly',trigger_config:{day:31,time:'09:00'},created_at:'2020-01-01T00:00:00Z',first_environment_ready:1}])
  const t=mount({setup(){schedule=useSchedule({tasks,me:ref(null),executions:ref([]),runtimeTab:ref('queue'),scheduleScope:ref('today'),view:ref('runtime')});return()=>h('div')}})
  assert.equal(schedule.buildScheduleDay('2027-02-28').entries.length,0)
  assert.equal(schedule.buildScheduleDay('2027-03-30').entries.length,0)
  const entries=schedule.buildScheduleDay('2027-03-31').entries;assert.equal(entries.length,1);assert.equal(entries[0].plannedAt,'2027-03-31T01:00:00.000Z')
  t.close()
})

await check('legacy expression stays intact and is not offered as a new schedule method',async()=>{
  const form=reactive(readPlan({trigger_type:'cron',trigger_config:{expression:'*/10 * * * *'}})),before=planBody(form),t=mount(PlanFields,{form})
  assert.deepEqual([...t.host.querySelector('select').options].filter(o=>!o.disabled).map(o=>o.value),['daily','weekly','monthly'])
  assert.equal(t.host.querySelector('select').selectedOptions[0].textContent,'保留原计划')
  assert(!t.host.querySelector('.cron-editor'));assert(!t.host.textContent.includes('Cron'))
  await pause();assert.deepEqual(planBody(form),before)
  click(t.host,'手动');await flush();click(t.host,'定时');await flush();assert.deepEqual(planBody(form),before);t.close()
})

await check('older monthly preview cannot overwrite a newer selected calendar day',async()=>{
  const pending=[];route=()=>new Promise(resolve=>pending.push(resolve))
  const form=reactive({...emptyPlan(),trigger_type:'monthly',monthly_day:31}),t=mount(PlanFields,{form})
  await pause();form.monthly_day=15;await pause();assert.equal(pending.length,2)
  pending[1](json({times:['2027-03-15T01:00:00Z']}));await flush()
  pending[0](json({times:['2027-03-31T01:00:00Z']}));await flush()
  assert.match(t.host.querySelector('li').textContent,/3\/15/);t.close()
})
console.log(`${passed} monthly schedule checks passed`)
dom.window.close()
