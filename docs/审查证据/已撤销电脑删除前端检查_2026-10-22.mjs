import assert from 'node:assert/strict'
import {JSDOM} from '../test-runtime/node_modules/jsdom/lib/api.js'
const dom=new JSDOM('<!doctype html><body></body>',{url:'http://127.0.0.1:18766/'})
for(const k of ['window','document','Node','Element','HTMLElement','SVGElement','Document','ShadowRoot','MutationObserver'])globalThis[k]=dom.window[k]
HTMLElement.prototype.getClientRects=function(){return this.isConnected?[{}]:[]}
const {createApp,nextTick,ref}=await import('vue')
const {workspaceKey}=await import('./src/workspace/context.js')
const {default:Hosts}=await import('./src/features/hosts/HostsPage.vue')
const {auditActionLabel}=await import('./src/lib/format.js')
const flush=async()=>{await nextTick();await new Promise(setImmediate);await nextTick()}
const json=(x,status=200)=>new Response(JSON.stringify(x),{status})
const button=(el,text)=>[...el.querySelectorAll('button')].find(x=>x.textContent.trim()===text)
let passed=0
async function check(name,fn){await fn();passed++;console.log('PASS '+name)}
function mount(handler=null,refreshFail=false){
 let hosts=['revoked','approved','pending','rejected'].map((status,i)=>({id:i+1,name:`合成电脑${i+1}`,approval_status:status,state:status,agent_version:'0.5.0',online:false,active_run_id:null}))
 let deleted=false,unauthorized=0;const calls=[]
 const history=[{id:7,host_id:1,host_name:'合成电脑1',task_name:'历史任务',status:'succeeded',created_at:'2026-10-01T00:00:00Z'}]
 globalThis.fetch=async(url,options={})=>{
  if(url==='/api/hosts')return deleted&&refreshFail?json({detail:'刷新失败'},503):json(hosts)
  if(url==='/api/remote-runs')return json(history)
  assert.equal(url,'/api/hosts/1');assert.equal(options.method,'DELETE');assert.equal(options.body,'{}');calls.push(url)
  const complete=()=>{deleted=true;hosts=hosts.filter(x=>x.id!==1);return json({deleted:true,id:1})}
  return handler?handler(complete):complete()
 }
 const el=document.createElement('div');document.body.append(el);const app=createApp(Hosts)
 app.provide(workspaceKey,{isSuperAdmin:ref(false),handleSessionExpired(){unauthorized++}});app.mount(el)
 return{el,calls,get unauthorized(){return unauthorized},async show(){await flush();const filter=el.querySelector('#host-filter');filter.value='all';filter.dispatchEvent(new window.Event('change',{bubbles:true}));await flush()},close(){app.unmount();el.remove()}}
}
await check('only revoked computers offer permanent deletion',async()=>{
 const t=mount();await t.show();assert.equal(t.el.querySelectorAll('.host-card').length,4)
 assert.equal([...t.el.querySelectorAll('button')].filter(x=>x.textContent==='永久删除').length,1)
 assert.match(button(t.el,'永久删除').closest('article').textContent,/合成电脑1/);t.close()
})
await check('opening confirmation and cancelling do not send a delete',async()=>{
 const t=mount();await t.show();button(t.el,'永久删除').click();await flush()
 const dialog=t.el.querySelector('[role=dialog]');assert(dialog);assert.match(dialog.textContent,/保留运行记录/);assert.match(dialog.textContent,/重新接入/)
 assert.equal(t.calls.length,0);button(dialog,'取消').click();await flush();assert(!t.el.querySelector('[role=dialog]'));assert.equal(t.calls.length,0);t.close()
})
await check('escape closes the confirmation before submitting',async()=>{
 const t=mount();await t.show();button(t.el,'永久删除').click();await flush()
 t.el.querySelector('[role=dialog]').dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await flush()
 assert(!t.el.querySelector('[role=dialog]'));assert.equal(t.calls.length,0);t.close()
})
await check('confirmed delete removes row and count while preserving run history',async()=>{
 const t=mount();await t.show();button(t.el,'永久删除').click();await flush();button(t.el,'确认永久删除').click();await flush();await flush()
 assert.equal(t.calls.length,1);assert(!t.el.querySelector('[role=dialog]'));assert(!button(t.el,'永久删除'))
 assert.equal(t.el.querySelectorAll('.host-card').length,3);assert.match(t.el.querySelector('.host-list-title').textContent,/3 台/)
 assert.match(t.el.querySelector('.host-runs').textContent,/历史任务/);t.close()
})
await check('bound-task conflict stays visible in the open dialog',async()=>{
 const t=mount(()=>json({detail:'仍有 1 个任务绑定这台电脑，请先修改运行电脑或删除任务'},409));await t.show()
 button(t.el,'永久删除').click();await flush();button(t.el,'确认永久删除').click();await flush()
 assert.match(t.el.querySelector('[role=dialog] [role=alert]').textContent,/1 个任务/);assert.equal(t.el.querySelectorAll('.host-card').length,4);t.close()
})
await check('busy state prevents duplicate deletion and closing midway',async()=>{
 let finish;const t=mount(complete=>new Promise(resolve=>{finish=()=>resolve(complete())}));await t.show()
 button(t.el,'永久删除').click();await flush();button(t.el,'确认永久删除').click();await flush()
 const dialog=t.el.querySelector('[role=dialog]');assert([...dialog.querySelectorAll('button')].every(b=>b.disabled))
 dialog.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await flush();assert(t.el.querySelector('[role=dialog]'))
 button(t.el,'删除中…').click();await flush();assert.equal(t.calls.length,1);finish();await flush();await flush();t.close()
})
await check('lost response allows retry without removing the row early',async()=>{
 let attempts=0;const t=mount(complete=>++attempts===1?json({detail:'连接中断'},503):complete());await t.show()
 button(t.el,'永久删除').click();await flush();button(t.el,'确认永久删除').click();await flush()
 assert.equal(t.el.querySelectorAll('.host-card').length,4);assert.match(t.el.querySelector('[role=dialog]').textContent,/连接中断/)
 button(t.el,'确认永久删除').click();await flush();await flush();assert.equal(t.calls.length,2);assert.equal(t.el.querySelectorAll('.host-card').length,3);t.close()
})
await check('successful deletion remains removed when the following refresh fails',async()=>{
 const t=mount(null,true);await t.show();button(t.el,'永久删除').click();await flush();button(t.el,'确认永久删除').click();await flush();await flush()
 assert(!button(t.el,'永久删除'));assert.equal(t.el.querySelectorAll('.host-card').length,3);assert.match(t.el.textContent,/刷新失败/);t.close()
})
await check('expired session follows the existing sign-in handling',async()=>{
 const t=mount(()=>json({detail:'登录已过期'},401));await t.show();button(t.el,'永久删除').click();await flush();button(t.el,'确认永久删除').click();await flush()
 assert.equal(t.unauthorized,1);assert.equal(t.el.querySelectorAll('.host-card').length,4);t.close()
})
await check('audit uses a Chinese deletion label',async()=>assert.equal(auditActionLabel('agent_delete'),'永久删除电脑'))
dom.window.close();console.log(`RESULT ${passed} passed`)
