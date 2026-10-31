// Run from an isolated frontend with check-loader.mjs and ../test-runtime/jsdom.
import assert from 'node:assert/strict'
import {JSDOM} from '../test-runtime/node_modules/jsdom/lib/api.js'
const dom=new JSDOM('<!doctype html><body></body>',{url:'http://127.0.0.1:18761/'})
for(const key of ['window','document','Node','Element','HTMLElement','SVGElement','MutationObserver']) globalThis[key]=dom.window[key]
const {createApp,h,reactive,nextTick,ref}=await import('vue')
const {default:PlanFields}=await import('./src/features/schedule/PlanFields.vue')
const {emptyPlan,readPlan,planBody,validatePlan}=await import('./src/lib/plans.js')
const {triggerDetail,shanghaiDateKey}=await import('./src/lib/format.js')
const {useSchedule}=await import('./src/features/schedule/useSchedule.js')
const {createTasksState,useTasks}=await import('./src/features/tasks/useTasks.js')
const calls=[]
globalThis.fetch=async(url,options={})=>{calls.push({url,...options});return new Response(JSON.stringify({times:['2026-10-28T01:00:00+00:00'],entries:[]}))}
const flush=async()=>{await nextTick();await new Promise(setImmediate);await nextTick()}
const change=(input,value)=>{input.value=value;input.dispatchEvent(new window.Event(input.tagName==='SELECT'?'change':'input',{bubbles:true}))}
let passed=0
async function check(name,run){await run();passed++;console.log('PASS '+name)}
await check('serialization and edit round trip for every interval unit',async()=>{
 for(const unit of ['seconds','minutes','hours','days']) {
  const saved={trigger_type:'interval',trigger_config:{value:15,unit}}
  assert.deepEqual(planBody(readPlan(saved)),saved)
  assert.equal(validatePlan(readPlan(saved),true),'')
  assert.match(triggerDetail(saved),/结束后间隔 15/)
 }
})
await check('invalid interval durations are rejected',async()=>{
 for(const value of [0,-1,1.5,'',100001,NaN]) assert.match(validatePlan({...emptyPlan(),trigger_type:'interval',interval_value:value}),/整数/)
 assert.match(validatePlan({...emptyPlan(),trigger_type:'interval',interval_unit:'weeks'}),/单位/)
})
for(const remote of [false,true]) await check(`real form changes and mode toggles, remote=${remote}`,async()=>{
 const form=reactive({...emptyPlan(),trigger_type:'interval'})
 const host=document.createElement('div');document.body.append(host)
 const app=createApp({render:()=>h(PlanFields,{form,remote})});app.mount(host);await flush()
 assert.deepEqual([...host.querySelector('select').options].map(x=>x.value),['interval','daily','weekly','monthly'])
 change(host.querySelector('input[type=number]'),'15');change(host.querySelectorAll('select')[1],'hours');await flush()
 assert.deepEqual(planBody(form),{trigger_type:'interval',trigger_config:{value:15,unit:'hours'}})
 assert.match(host.textContent,/运行结束后重新计时/)
 assert.equal(host.querySelector('details'),null)
 assert.equal(host.querySelector('input[type=time]'),null)
 host.querySelectorAll('button')[0].click();await flush();assert.equal(form.trigger_type,'manual')
 host.querySelectorAll('button')[1].click();await flush();assert.equal(form.trigger_type,'interval');assert.equal(form.interval_value,15)
 assert(!calls.some(c=>c.body && JSON.parse(c.body).trigger_type==='interval'))
 app.unmount();host.remove()
})
await check('disabled workflow interval restores its saved form',async()=>{
 const form=readPlan({trigger_type:'interval',trigger_config:{value:2,unit:'hours'},schedule_enabled:false},true)
 assert.equal(form.trigger_type,'manual');assert.equal(form.saved_trigger_type,'interval');assert.equal(form.interval_value,2)
})
await check('the full task editor restores the saved duration instead of its defaults',async()=>{
 const state=createTasksState(),api=useTasks({...state,me:ref({id:1}),saving:ref(false),taskIsActive:()=>false,isAdmin:ref(true),loadAll:async()=>{},showToast:()=>{}})
 api.openEdit({id:3,name:'synthetic',enabled:false,trigger_type:'interval',trigger_config:{value:2,unit:'seconds'}})
 assert.equal(state.taskForm.interval_value,2);assert.equal(state.taskForm.interval_unit,'seconds')
 assert.deepEqual(planBody(state.taskForm),{trigger_type:'interval',trigger_config:{value:2,unit:'seconds'}})
 api.resetTaskSession()
})
await check('timetable shows only a known next occurrence and hides uncomputed times',async()=>{
 const now=new Date(),point=new Date(now.getTime()+60000).toISOString()
 const tasks=ref([{id:1,name:'known',enabled:true,trigger_type:'interval',next_run_at:point},
 {id:2,name:'running',enabled:true,trigger_type:'interval',next_run_at:null}])
 let state
 const app=createApp({setup(){state=useSchedule({tasks,executions:ref([]),me:ref(null),view:ref('runtime'),runtimeTab:ref('queue'),scheduleScope:ref('today')});return()=>h('div')}})
 const host=document.createElement('div');app.mount(host);await flush()
 const entries=state.taskScheduleDays.value.flatMap(day=>day.entries)
 assert.equal(entries.length,shanghaiDateKey(now)===shanghaiDateKey(point)?1:0)
 if(entries.length)assert.equal(entries[0].task.id,1)
 app.unmount()
})
console.log(`Passed ${passed} interval frontend checks`)
