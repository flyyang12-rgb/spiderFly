// Copy into a fresh isolated frontend beside check-loader.mjs.
import assert from 'node:assert/strict'
import { JSDOM } from '../test-runtime/node_modules/jsdom/lib/api.js'
const dom=new JSDOM('<!doctype html><body></body>',{url:'http://127.0.0.1:18763/'})
for(const k of ['window','document','Node','Element','HTMLElement','SVGElement','Document','ShadowRoot','MutationObserver','FormData'])globalThis[k]=dom.window[k]
const {createApp,nextTick,ref}=await import('vue')
const {workspaceKey}=await import('./src/workspace/context.js')
const {default:Hosts}=await import('./src/features/hosts/HostsPage.vue')
const flush=async()=>{await nextTick();await new Promise(setImmediate);await nextTick()}
const base={id:1,name:'验收电脑 B',approval_status:'approved',state:'idle',online:true,agent_version:'0.5.0',current_agent_version:'0.5.1',remote_upgrade_supported:true,agent_update_available:true,dispatch_enabled:true,agent_dispatch_enabled:true,interactive_session:true,active_run_id:null,upgrade_pending:false}
let passed=0
async function check(name,fn){await fn();passed++;console.log('PASS '+name)}
function mount(initial={},superAdmin=true,handler=null){
 let host={...base,...initial};const calls=[]
 globalThis.fetch=async(url,options={})=>{
  if(url==='/api/hosts')return new Response(JSON.stringify([host]),{status:200})
  if(url==='/api/remote-runs')return new Response('[]',{status:200})
  assert.equal(url,'/api/hosts/1/upgrade');assert.equal(options.method,'POST');calls.push(JSON.parse(options.body))
  if(handler)return handler(calls,()=>{host={...host,state:'upgrading',upgrade_pending:true,agent_update:{id:'job',status:'queued',target_version:'0.5.1'}}})
  host={...host,state:'upgrading',upgrade_pending:true,agent_update:{id:'job',status:'queued',target_version:'0.5.1'}}
  return new Response(JSON.stringify(host.agent_update),{status:200})
 }
 const el=document.createElement('div');document.body.append(el);const app=createApp(Hosts)
 app.provide(workspaceKey,{isSuperAdmin:ref(superAdmin),handleSessionExpired(){throw new Error('Unexpected auth failure')}});app.mount(el)
 return {el,calls,button:()=>[...el.querySelectorAll('button')].find(x=>x.textContent==='升级'),set(v){host={...host,...v}},close(){app.unmount();el.remove()}}
}
await check('online idle supported agent offers one click and freezes while submitted',async()=>{const t=mount();await flush();assert.ok(t.button());assert.equal(t.button().disabled,false);t.button().click();await flush();assert.match(t.calls[0].request_id,/^[a-f0-9]{32}$/);assert.equal(t.button().disabled,true);assert.match(t.el.textContent,/等待升级/);t.close()})
await check('old agent explains bootstrap once and cannot submit',async()=>{const t=mount({agent_version:'0.4.1',remote_upgrade_supported:false});await flush();assert.equal(t.button().disabled,true);assert.match(t.el.textContent,/首次需在目标电脑更新到 0.5.0/);assert.equal(t.calls.length,0);t.close()})
await check('offline and active agents cannot start an upgrade',async()=>{for(const props of [{online:false},{active_run_id:8},{upgrade_pending:true}]){const t=mount(props);await flush();assert.equal(t.button().disabled,true);t.close()}})
await check('business administrators have no deployment control',async()=>{const t=mount({},false);await flush();assert.equal(t.button(),undefined);t.close()})
await check('lost response retry retains the same request identifier',async()=>{const t=mount({},true,(calls,update)=>{if(calls.length===1)return new Response(JSON.stringify({detail:'连接中断，请重试'}),{status:503});update();return new Response(JSON.stringify({id:'job',status:'queued'}),{status:200})});await flush();t.button().click();await flush();assert.match(t.el.textContent,/连接中断/);t.button().click();await flush();assert.equal(t.calls.length,2);assert.equal(t.calls[0].request_id,t.calls[1].request_id);t.close()})
await check('confirmed new version replaces old version and removes update action',async()=>{const t=mount();await flush();t.set({agent_version:'0.5.1',agent_update_available:false,agent_update:{id:'job',status:'succeeded',target_version:'0.5.1'}});[...t.el.querySelectorAll('button')].find(x=>x.textContent==='刷新').click();await flush();assert.equal(t.button(),undefined);assert.match(t.el.textContent,/Agent 0.5.1/);assert.match(t.el.textContent,/已升级至 0.5.1/);t.close()})
await check('failure details stay folded and preserve the real error',async()=>{const t=mount({agent_update:{id:'job',status:'failed',target_version:'0.5.1',message:'新版本启动失败；旧版已恢复运行'}});await flush();const details=[...t.el.querySelectorAll('details')].find(x=>x.querySelector('summary')?.textContent==='升级失败');assert.ok(details);assert.equal(details.open,false);assert.match(details.textContent,/旧版已恢复运行/);t.close()})
dom.window.close();console.log(`RESULT ${passed} passed; compiled Vue / synthetic requests / no real upgrade`)
