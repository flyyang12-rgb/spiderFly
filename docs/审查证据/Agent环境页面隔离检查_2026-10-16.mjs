// Copy into an isolated frontend with the existing isolated SFC loader.
import assert from 'node:assert/strict'
import { JSDOM } from '../test-runtime/node_modules/jsdom/lib/api.js'
const dom=new JSDOM('<!doctype html><body></body>',{url:'http://127.0.0.1:18763/'})
for(const key of ['window','document','Node','Element','HTMLElement','SVGElement','Document','ShadowRoot','MutationObserver'])globalThis[key]=dom.window[key]
const {createApp,h,reactive,nextTick}=await import('vue')
const {default:Environment}=await import('./src/features/hosts/EnvironmentDetails.vue')
const {default:Dependencies}=await import('./src/features/apps/DependencyDetails.vue')
const flush=async()=>{await nextTick();await new Promise(setImmediate);await nextTick()}
const facts={status:'ready',python:'3.12.10',bits:64,packages:[{name:'requests',version:'2.32.5'}],cache_reused:false,verified_at:'2026-10-16T00:00:00+00:00',requirements_hash:'a'.repeat(64)}
function mount(component,initial){const props=reactive(initial),el=document.createElement('div');document.body.append(el);const app=createApp({render:()=>h(component,props)});app.mount(el);return {props,el,close(){app.unmount();el.remove()}}}
let passed=0
async function check(name,fn){await fn();passed++;console.log('PASS '+name)}
await check('environment facts start folded and show installed versions',async()=>{const t=mount(Environment,{environment:facts});assert.equal(t.el.querySelector('details').open,false);assert.match(t.el.textContent,/已校验/);assert.match(t.el.querySelector('pre').textContent,/requests==2.32.5/);assert.match(t.el.textContent,/3.12.10.*64/);t.close()})
await check('cached environment explicitly says it was rechecked',async()=>{const t=mount(Environment,{environment:{...facts,cache_reused:true}});assert.match(t.el.textContent,/复用缓存并重新校验/);t.close()})
await check('failed checks retain exact stage and error without showing ready',async()=>{const t=mount(Environment,{environment:{...facts,status:'failed',stage:'pip_check',error:'synthetic dependency conflict'}});assert.match(t.el.querySelector('summary').textContent,/准备失败/);assert.match(t.el.textContent,/依赖一致性检查：synthetic dependency conflict/);t.close()})
await check('old runs are unreported rather than inferred ready',async()=>{const t=mount(Environment,{environment:null,status:'unreported'});assert.match(t.el.textContent,/未提供/);assert.match(t.el.textContent,/旧 Agent.*0.4.0/);assert.equal(t.el.querySelector('pre'),null);t.close()})
await check('pending target remains pending',async()=>{const t=mount(Environment,{environment:null,status:'pending'});assert.match(t.el.textContent,/待回传/);assert.doesNotMatch(t.el.textContent,/已校验/);t.close()})
await check('switching reports replaces packages and labels',async()=>{const t=mount(Environment,{environment:facts,label:'电脑 B · v1'});t.props.environment={...facts,packages:[{name:'Pillow',version:'12.0.0'}]};t.props.label='电脑 C · v2';await flush();assert.match(t.el.textContent,/电脑 C · v2/);assert.match(t.el.textContent,/Pillow==12.0.0/);assert.doesNotMatch(t.el.textContent,/requests==/);t.close()})
await check('task details keep controller and target environment records separate',async()=>{globalThis.fetch=async()=>new Response(JSON.stringify({versions:[],environments:[],remote_environments:[{run_id:7,host_name:'验收电脑',version_sequence:2,environment:facts}]}),{status:200,headers:{'Content-Type':'application/json'}});const t=mount(Dependencies,{appId:1,remote:true});const details=t.el.querySelector('details');details.open=true;details.dispatchEvent(new window.Event('toggle'));await flush();assert.match(t.el.textContent,/尚无本机环境记录/);assert.match(t.el.textContent,/验收电脑 · v2 · 运行 #7/);assert.match(t.el.textContent,/requests==2.32.5/);t.close()})
console.log(`RESULT ${passed} passed; isolated compiled Vue / synthetic environment records`)
