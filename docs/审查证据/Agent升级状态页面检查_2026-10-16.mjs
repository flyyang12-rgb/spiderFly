// Copy beside check-loader.mjs in a fresh isolated frontend.
import assert from 'node:assert/strict'
import { JSDOM } from '../test-runtime/node_modules/jsdom/lib/api.js'
const dom=new JSDOM('<!doctype html><body></body>',{url:'http://127.0.0.1:18763/'})
for(const key of ['window','document','Node','Element','HTMLElement','SVGElement','Document','ShadowRoot','MutationObserver'])globalThis[key]=dom.window[key]
const {createApp,nextTick}=await import('vue')
const {workspaceKey}=await import('./src/workspace/context.js')
const {default:Hosts}=await import('./src/features/hosts/HostsPage.vue')
const requests=[]
globalThis.fetch=async url=>{
 requests.push(url)
 assert.ok(['/api/hosts','/api/remote-runs'].includes(url))
 const value=url==='/api/hosts'?[{id:1,name:'合成升级电脑',approval_status:'approved',state:'upgrading',upgrade_pending:true,online:true,dispatch_enabled:true,agent_dispatch_enabled:true,interactive_session:true}]:[]
 return new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}})
}
const el=document.createElement('div');document.body.append(el)
const app=createApp(Hosts);app.provide(workspaceKey,{handleSessionExpired(){throw new Error('Unexpected auth failure')}})
try{
 app.mount(el);await nextTick();await new Promise(setImmediate);await nextTick()
 assert.match(el.textContent,/合成升级电脑/);assert.match(el.textContent,/升级中/);assert.match(el.textContent,/暂缓新派发/)
 assert.equal(requests.length,2)
 console.log('RESULT 1 passed; compiled host page shows upgrade pause; synthetic HTTP only')
}finally{app.unmount();el.remove();dom.window.close()}
