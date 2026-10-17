import assert from 'node:assert/strict'
import { JSDOM } from '../test-runtime/node_modules/jsdom/lib/api.js'
import { File } from 'node:buffer'
const dom=new JSDOM('<!doctype html><body></body>',{url:'http://127.0.0.1:18763/'})
for(const key of ['window','document','Node','Element','HTMLElement','SVGElement','Document','ShadowRoot','MutationObserver'])globalThis[key]=dom.window[key]
globalThis.File=File
const {createApp,h,reactive,ref,nextTick}=await import('vue')
const {default:Fields}=await import('./src/features/apps/DependencyFields.vue')
const {createAppsState,useApps}=await import('./src/features/apps/useApps.js')
const flush=async()=>{await nextTick();await new Promise(setImmediate);await nextTick()}
const pause=async()=>{await new Promise(r=>setTimeout(r,340));await flush()}
const response=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}})
const file=name=>new File(['import requests'],name,{type:'text/x-python'})
const automatic={ready:true,requirements:'requests',unresolved:[],items:[{id:'import:requests',module:'requests',kind:'import',package:'requests',lines:[1],status:'automatic'}],target:{kind:'controller',version:'3.12.10'}}
const unknown={...automatic,ready:false,requirements:'',unresolved:['unknown:biz'],items:[{id:'unknown:biz',module:'biz',kind:'unknown',package:'',lines:[3],reason:'无法确认模块对应的安装包'}]}
let route=()=>response(automatic),calls=[],passed=0
globalThis.fetch=async(url,options={})=>{calls.push({url,...options});return route(url,options)}
function mount(extra={}){
 const props=reactive({script:file('one.py'),requirements:'',target:'',modelValue:[],...extra}),states=[]
 const host=document.createElement('div');document.body.append(host)
 const app=createApp({render:()=>h(Fields,{...props,'onUpdate:modelValue':v=>props.modelValue=v,'onUpdate:requirements':v=>props.requirements=v,onState:v=>states.push(v),onUnauthorized:()=>states.push({unauthorized:true})})})
 app.mount(host);return {props,states,host,close(){app.unmount();host.remove()}}
}
function change(el,value){el.value=value;el.dispatchEvent(new window.Event(el.tagName==='SELECT'?'change':'input',{bubbles:true}))}
async function check(name,fn){calls=[];route=()=>response(automatic);await fn();passed++;console.log('PASS '+name)}
await check('file automatically analyzes without running or per-package confirmations',async()=>{
 const t=mount();await pause();assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/apps/analyze-dependencies');assert.equal(calls[0].body.get('script').name,'one.py')
 assert.match(t.host.textContent,/已识别 1 项依赖/);assert.equal(t.host.querySelectorAll('select').length,0);assert.equal(t.states.at(-1).ready,true);assert.equal(t.host.querySelector('details').open,false);t.close()
})
await check('file replacement invalidates synchronously and rejects stale response',async()=>{
 let resolveOld;route=()=>new Promise(r=>resolveOld=r);const t=mount();await pause();const first=calls[0]
 t.props.script=file('two.py');await flush();assert.equal(t.states.at(-1).ready,false);assert.equal(first.signal.aborted,true)
 route=()=>response({...automatic,requirements:'pandas'});await pause();resolveOld(response(unknown));await flush();assert.match(t.host.textContent,/已识别 1 项/);assert.doesNotMatch(t.host.textContent,/biz/);t.close()
})
await check('explicit requirements changed in form invalidate and send exact user constraints',async()=>{
 const t=mount();await pause();t.props.requirements='requests==2.32.5\nopenpyxl';await flush();assert.equal(t.states.at(-1).ready,false);await pause()
 assert.equal(calls.at(-1).body.get('requirements_text'),t.props.requirements);assert.equal(t.props.script.name,'one.py');t.close()
})
await check('unknown module keeps row visible while package choice incomplete',async()=>{
 route=()=>response(unknown);const t=mount();await pause();assert.match(t.host.textContent,/第 3 行/)
 change(t.host.querySelector('select'),'package');await pause();assert.equal(t.host.querySelectorAll('article').length,1);assert.ok(t.host.querySelector('article input'));assert.equal(t.states.at(-1).ready,false)
 route=()=>response({...automatic,items:[{...unknown.items[0],status:'resolved',requirement:'my-business>=1'}]})
 change(t.host.querySelector('article input'),'my-business>=1');await pause();assert.equal(t.states.at(-1).ready,true)
 assert.deepEqual(JSON.parse(calls.at(-1).body.get('dependency_decisions')),[{id:'unknown:biz',action:'package',requirement:'my-business>=1',reason:''}]);t.close()
})
await check('explicit exclusion requires and preserves a reason',async()=>{
 route=()=>response(unknown);const t=mount();await pause();change(t.host.querySelector('select'),'exclude');await pause();assert.equal(t.states.at(-1).ready,false)
 route=()=>response({...automatic,requirements:'',items:[{...unknown.items[0],status:'excluded',exclusion_reason:'外部业务目录自行提供'}]})
 change(t.host.querySelector('article input'),'外部业务目录自行提供');await pause();assert.equal(t.states.at(-1).ready,true);assert.equal(t.props.modelValue[0].reason,'外部业务目录自行提供');t.close()
})
await check('target changes clear old decisions and bind the new target',async()=>{
 route=()=>response(unknown);const t=mount();await pause();t.props.modelValue=[{id:'unknown:biz',action:'exclude',reason:'old computer'}];await pause()
 t.props.target='7';await pause();assert.deepEqual([...t.props.modelValue],[]);assert.equal(calls.at(-1).body.get('target_host_id'),'7');t.close()
})
await check('version analysis uses task identity while target polling invalidates',async()=>{
 const t=mount({taskId:42,target:'3'});await pause();assert.equal(calls.at(-1).body.get('task_id'),'42');assert.equal(calls.at(-1).body.get('target_host_id'),null)
 t.props.target='9';await pause();assert.equal(calls.length,2);t.close()
})
await check('analysis failure retains source and supplemental text',async()=>{
 route=()=>response({detail:'Python 语法错误：第 2 行'},400);const t=mount({requirements:'openpyxl'});await pause();assert.match(t.host.textContent,/第 2 行/);assert.equal(t.props.script.name,'one.py');assert.equal(t.props.requirements,'openpyxl');assert.equal(t.states.at(-1).ready,false);t.close()
})
await check('unauthorized analysis expires session without clearing user source',async()=>{
 route=()=>response({detail:'请先登录'},401);const t=mount();await pause();assert.equal(t.states.at(-1).unauthorized,true);assert.ok(t.props.script);t.close()
})
await check('unmount aborts and suppresses late results',async()=>{
 let resolve;route=()=>new Promise(r=>resolve=r);const t=mount();await pause();t.close();const count=t.states.length;resolve(response(automatic));await flush();assert.equal(t.states.length,count);assert.equal(calls[0].signal.aborted,true)
})
await check('supplement file rejects invalid UTF8 without replacing user text',async()=>{
 const t=mount({requirements:'openpyxl'});await pause();const input=t.host.querySelector('input[type=file]')
 Object.defineProperty(input,'files',{value:[new File([new Uint8Array([255])],'requirements.txt')]});input.dispatchEvent(new window.Event('change',{bubbles:true}));await flush();assert.equal(t.props.requirements,'openpyxl');assert.ok(t.host.querySelector('[role=alert]'));t.close()
})
await check('save waits for analysis and preserves exact dependency decisions',async()=>{
 const state=createAppsState(),toasts=[],owner={id:1};const me=ref(owner)
 Object.assign(state.appForm,{name:'synthetic',script:file('new.py'),dependency_status:'loading'})
 const api=useApps({...state,me,isAdmin:ref(true),loadAll:async()=>{},showToast:(...v)=>toasts.push(v)})
 assert.equal(await api.uploadApp(),null);assert.equal(calls.length,0)
 state.appForm.dependency_status='ready';state.appForm.dependency_decisions=[{id:'unknown:biz',action:'exclude',reason:'outside business directory'}]
 route=()=>response({task:{id:9},environment_status:'failed'})
 assert.equal((await api.uploadApp()).id,9);assert.deepEqual(JSON.parse(calls[0].body.get('dependency_decisions')),state.appForm.dependency_decisions)
 assert.match(toasts.at(-1).join(' '),/任务已保存.*依赖安装失败/);api.resetCreateSession()
})
await check('malformed analysis response becomes a visible error instead of crashing',async()=>{
 route=()=>response({ready:true});const t=mount();await pause();assert.match(t.host.textContent,/响应不完整/);assert.equal(t.states.at(-1).ready,false);t.close()
})
await check('reading supplemental file invalidates old ready and identical content reanalyzes',async()=>{
 const t=mount({requirements:'openpyxl'});await pause();const input=t.host.querySelector('input[type=file]');let finish
 const f=new File(['openpyxl'],'requirements.txt');f.arrayBuffer=()=>new Promise(r=>finish=r)
 Object.defineProperty(input,'files',{value:[f]});input.dispatchEvent(new window.Event('change',{bubbles:true}));await flush()
 assert.equal(t.states.at(-1).ready,false);assert.equal(t.host.querySelector('textarea').disabled,true)
 finish(new TextEncoder().encode('openpyxl').buffer);await pause();assert.equal(t.props.requirements,'openpyxl');assert.equal(t.states.at(-1).ready,true);assert.equal(calls.length,2);t.close()
})
console.log(JSON.stringify({passed}))
