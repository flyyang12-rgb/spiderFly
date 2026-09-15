<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { updateContext, repairFor } from './lib/versionUpdates'
const props=defineProps({taskId:{type:Number,required:true}})
const state=ref(null), error=ref(''), busy=ref(false), uploadFile=ref(null), requirements=ref(''), evidence=ref(''), showUpload=ref(false), detail=ref(null)
const names={request:'原始要求',summary:'用途',input:'输入',urls:'站点',scope:'范围',quantity:'数量',fields:'字段',filters:'筛选',sort:'排序',concurrency:'并发',output:'输出',acceptance:'验收'}
const origins={user:'用户要求',inferred:'从代码推断',unspecified:'未说明'}
const labels={pending:'等待验证',testing:'正在验证',repairing:'正在修复',conflict:'需要确认差异',ready:'验证通过，等待确认',activated:'已启用',failed:'更新失败',cancelled:'已取消',interrupted:'已中断'}
const latestUpdate=computed(()=>state.value?.updates?.[0])
const updateNotice=computed(()=>state.value ? updateContext(state.value,latestUpdate.value) : null)
const uploadNotice=ref(''), loadError=ref('')
let timer,alive=true
async function api(path,options={}) {
 const r=await fetch('/api/task-versions'+path,{credentials:'include',...options});const value=await r.json()
 if(!r.ok)throw new Error(typeof value.detail==='string'?value.detail:'版本请求失败')
 return value
}
async function load(){try{const value=await api('/tasks/'+props.taskId);if(alive){state.value=value;loadError.value=''}}catch(e){if(alive)loadError.value=e.message}}
function openUpload(){requirements.value=state.value?.versions.find(v=>v.id===state.value.active_version_id)?.requirements||'';showUpload.value=!showUpload.value}
async function upload(){
 if(!uploadFile.value)return
 busy.value=true;error.value=''
 try{const form=new FormData();form.append('script',uploadFile.value);form.append('requirements',requirements.value);form.append('evidence',evidence.value);form.append('base_version_id',state.value.active_version_id)
 const result=await api('/tasks/'+props.taskId+'/upload',{method:'POST',body:form});uploadNotice.value=`V${result.version} 已提交。验证失败会尝试自动修复，修复版需确认后使用。`;showUpload.value=false;await load()
 }catch(e){error.value=e.message}finally{busy.value=false}
}
async function action(path){busy.value=true;error.value='';uploadNotice.value='';try{await api(path,{method:'POST'});await load()}catch(e){error.value=e.message}finally{busy.value=false}}
async function rollback(id){busy.value=true;error.value='';try{const r=await fetch(`/api/maintenance/tasks/${props.taskId}/rollback/${id}`,{method:'POST',credentials:'include'});const v=await r.json();if(!r.ok)throw new Error(v.detail||'回退失败');await load()}catch(e){error.value=e.message}finally{busy.value=false}}
async function activate(id){await action(`/updates/${id}/activate`)}
async function dismiss(id){await action(`/updates/${id}/stop`)}
function formatted(value){return value==null?'未说明':typeof value==='object'?JSON.stringify(value):String(value)}
function versionState(version){
 if(version.id===state.value?.active_version_id)return {label:'使用中',tone:'current'}
 if(version.update_status==='ready')return {label:'待确认',tone:'pending'}
 if(repairFor(state.value,version))return {label:'验证未通过',tone:'failed'}
 if(version.approved)return {label:'可切换',tone:'approved'}
 return {label:'未启用',tone:'draft'}
}
function versionBase(version){
 if(!version.base_version_id)return '原始版本'
 const base=state.value?.versions.find(item=>item.id===version.base_version_id)
 return base?`V${base.sequence}`:'历史版本'
}
function specRows(source){
 let spec
 try{spec=typeof source==='string'?JSON.parse(source):source}catch{return []}
 if(!spec||typeof spec!=='object')return []
 return Object.entries(spec).filter(([,item])=>item?.value!==null&&item?.value!==undefined&&item?.value!=='').map(([key,item])=>({key,label:names[key]||key,value:formatted(item.value),origin:origins[item.origin]||item.origin||'未说明'}))
}
function requirementRows(version){return specRows(version.spec)}
onMounted(()=>{load();timer=setInterval(load,2500)})
onBeforeUnmount(()=>{alive=false;clearInterval(timer)})
</script>
<template>
 <section class="task-versions" v-if="state">
  <p v-if="error" role="alert">{{error}}</p>
  <p v-if="loadError" role="alert">{{loadError}}</p>
  <p v-if="uploadNotice && (!latestUpdate || ['pending','testing','repairing'].includes(latestUpdate.status))" class="version-update-notice" role="status">{{uploadNotice}}</p>
  <div v-if="updateNotice" class="version-update-notice" :class="{ 'needs-attention': updateNotice.repaired || ['repairing','failed','ready'].includes(latestUpdate.status) }" role="status">
   <strong>{{updateNotice.title}}</strong>
   <small>{{updateNotice.current}}</small>
   <div v-if="latestUpdate.status==='ready'" class="version-notice-actions"><button class="button compact" :disabled="busy" @click="activate(latestUpdate.id)">确认使用 V{{latestUpdate.sequence}}</button><button class="button ghost compact" :disabled="busy" @click="dismiss(latestUpdate.id)">暂不使用</button></div>
   <details v-if="(updateNotice.repaired || ['repairing','failed'].includes(latestUpdate.status)) && updateNotice.originalLog"><summary>查看上传版本的验证信息</summary><pre>{{updateNotice.originalLog}}</pre></details>
  </div>
  <details class="version-section">
   <summary class="version-section-summary">
    <span class="summary-chevron" aria-hidden="true"></span>
    <span class="summary-copy"><strong>代码版本</strong><small>{{state.versions.length}} 个版本 · 当前 V{{state.versions.find(v=>v.id===state.active_version_id)?.sequence||'—'}}</small></span>
   </summary>
   <div class="version-section-body">
    <div class="version-toolbar">
     <div><strong>版本历史</strong><p>上传、确认使用、下载或切换任务脚本</p></div>
     <button type="button" class="button version-upload-trigger" :aria-expanded="showUpload" @click="openUpload"><span aria-hidden="true">＋</span>{{showUpload?'收起更新':'更新脚本'}}</button>
    </div>
    <form v-if="showUpload" @submit.prevent="upload" class="version-upload">
     <div class="version-upload-heading"><strong>上传新版本</strong><small>先验证；失败会尝试生成修复版，验证通过后由你确认使用</small></div>
     <div class="version-upload-grid">
      <label class="version-file-picker" :class="{ selected: uploadFile }">
       <input type="file" accept=".py" required @change="uploadFile=$event.target.files[0]">
       <span class="version-file-mark">PY</span>
       <span><strong>Python 脚本</strong><small>{{uploadFile?.name||'选择新的 .py 文件'}}</small></span>
       <b>{{uploadFile?'更换':'选择文件'}}</b>
      </label>
      <label>修改说明（可选）<input v-model="evidence" placeholder="例如：修正翻页逻辑"></label>
      <label class="upload-requirements">Python 依赖<textarea v-model="requirements" rows="2" placeholder="沿用原依赖或填写新依赖"></textarea></label>
     </div>
     <div class="version-upload-actions"><button type="button" class="button ghost" @click="showUpload=false">取消</button><button class="button" :disabled="busy">{{busy?'正在提交…':'上传并验证'}}</button></div>
    </form>
    <div class="version-list">
     <article class="version-card" :class="`is-${versionState(v).tone}`" v-for="v in state.versions" :key="v.id">
      <span class="version-marker" aria-hidden="true"></span>
      <div class="version-card-main">
       <div class="version-identity">
        <strong>V{{v.sequence}}</strong>
        <span class="version-badge" :class="`is-${versionState(v).tone}`">{{versionState(v).label}}</span>
        <span v-if="v.origin==='repair'||v.kind==='repair'" class="version-badge">自动修复</span>
        <span v-else-if="v.origin==='upload'||v.kind==='upload'" class="version-badge">手动上传</span>
       </div>
       <div class="version-meta"><span>运行环境：{{v.runtime||'原任务环境'}}</span><span>基于：{{versionBase(v)}}</span></div>
       <p v-if="repairFor(state,v)" class="version-lineage">验证未通过，已生成修复版 V{{repairFor(state,v).sequence}}</p>
       <p v-else-if="v.origin==='repair'" class="version-lineage">由 {{versionBase(v)}} 自动修复生成</p>
      </div>
      <div class="version-actions">
       <a class="version-action" :href="`/api/task-versions/versions/${v.id}/download`"><span aria-hidden="true">↓</span> 下载 PY</a>
       <button type="button" class="version-action" :aria-expanded="detail===v.id" @click="detail=detail===v.id?null:v.id">{{detail===v.id?'收起':'详情'}}</button>
       <template v-if="v.update_status==='ready'&&v.update_id"><button type="button" class="version-action" :disabled="busy" @click="dismiss(v.update_id)">暂不使用</button><button type="button" class="version-action activate" :disabled="busy" @click="activate(v.update_id)">确认使用</button></template>
       <button type="button" class="version-action rollback" v-else-if="v.approved&&v.id!==state.active_version_id" :disabled="busy" @click="rollback(v.id)">{{v.sequence<(state.versions.find(item=>item.id===state.active_version_id)?.sequence||0)?'回退至此':'使用此版本'}}</button>
      </div>
      <div class="version-detail" v-if="detail===v.id">
       <div class="version-detail-heading"><strong>保存的任务信息</strong><small>随 V{{v.sequence}} 一起保存</small></div>
       <div v-if="requirementRows(v).length" class="version-requirements"><article v-for="item in requirementRows(v)" :key="item.key"><span>{{item.label}}</span><strong>{{item.value}}</strong><small>{{item.origin}}</small></article></div>
       <div v-else class="version-empty-requirements">该版本没有记录具体任务要求</div>
      </div>
     </article>
    </div>
   </div>
  </details>
  <details v-if="state.updates.length"><summary>更新记录 · {{labels[state.updates[0].status]}}</summary>
   <article v-for="item in state.updates" :key="item.id">
    <p>{{item.note||labels[item.status]}}</p>
    <template v-if="item.status==='conflict'"><button class="button ghost" :disabled="busy" @click="action(`/updates/${item.id}/resolve`)">按新代码同步要求</button><button class="button ghost" @click="action(`/updates/${item.id}/stop`)">保留原要求</button></template>
    <button v-if="['pending','testing','repairing'].includes(item.status)" class="button ghost" @click="action(`/updates/${item.id}/stop`)">停止本次更新</button>
    <details v-if="item.log"><summary>运行日志</summary><pre>{{item.log}}</pre></details>
   </article>
  </details>
  <details v-if="state.deliveries?.length"><summary>群通知记录</summary><p v-for="item in state.deliveries" :key="item.event_key">{{item.note||item.status}}</p></details>
 </section>
</template>
<style scoped>
.version-update-notice{margin:10px 0;padding:12px 14px;border:1px solid #d5e5da;border-radius:9px;background:#f4faf6;line-height:1.7;overflow-wrap:anywhere}
.version-update-notice.needs-attention{background:#fffaf1;border-color:#ead8b6}
.version-update-notice>strong,.version-update-notice>small{display:block}
.version-update-notice>small{color:#738277;margin-top:3px}
.version-update-notice details{margin-top:7px}.version-update-notice summary{cursor:pointer}
.version-update-notice pre{max-height:220px;overflow:auto;white-space:pre-wrap;font-size:12px}
.version-notice-actions{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}
.version-lineage{font-size:12px;margin:7px 0 0;color:#80613a}
.version-badge.is-failed{color:#a33c2f;background:#fff0ed}
.task-versions{margin-top:10px;font-size:13px;color:#385f49}.task-versions>details:not(.version-section){margin:12px 0}.task-versions>details:not(.version-section)>summary{cursor:pointer}.task-versions>p[role="alert"]{padding:10px 12px;border:1px solid #f0c9c3;border-radius:8px;background:#fff6f4;color:#a33c2f}dl{display:grid;grid-template-columns:150px 1fr;gap:8px}dt small{display:block;color:#77877b}dd{margin:0;overflow-wrap:anywhere;white-space:pre-wrap}
.version-section{margin:14px 0;border:1px solid #dce7df;border-radius:14px;background:#fff;overflow:hidden;box-shadow:0 8px 24px rgba(36,73,51,.045)}.version-section-summary{display:flex;align-items:center;gap:11px;padding:15px 17px;cursor:pointer;list-style:none;background:#fbfdfb;transition:background .18s ease}.version-section-summary::-webkit-details-marker{display:none}.version-section-summary:hover{background:#f6faf7}.summary-chevron{width:8px;height:8px;border-right:1.5px solid #54705f;border-bottom:1.5px solid #54705f;transform:rotate(-45deg);transition:transform .18s ease}.version-section[open] .summary-chevron{transform:rotate(45deg) translate(-2px,-2px)}.summary-copy{display:flex;align-items:baseline;gap:10px;min-width:0}.summary-copy strong{font-size:14px;color:#264b36}.summary-copy small{font-size:12px;color:#849187}.version-section-body{padding:16px 17px 18px;border-top:1px solid #e7ede9}
.version-toolbar{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:14px}.version-toolbar strong{display:block;font-size:14px;color:#223d2d}.version-toolbar p{margin:4px 0 0!important;font-size:12px!important;line-height:1.5!important;color:#7b897f!important}.version-upload-trigger{display:inline-flex;align-items:center;justify-content:center;gap:5px;min-width:108px;white-space:nowrap}.version-upload-trigger span{font-size:16px;line-height:1}
.version-upload{display:grid;gap:14px;margin-bottom:16px;padding:16px;border:1px solid #dce8e0;border-radius:12px;background:#f8fbf9}.version-upload-heading{display:flex;align-items:baseline;gap:9px}.version-upload-heading strong{font-size:14px;color:#244831}.version-upload-heading small{color:#7a8b80}.version-upload-grid{display:grid;grid-template-columns:1fr 1fr;gap:13px}.version-upload label{display:grid;gap:7px;color:#466653;font-weight:600}.version-upload .upload-requirements{grid-column:1/-1}.version-upload textarea,.version-upload input:not([type="file"]){box-sizing:border-box;width:100%;padding:9px 10px;border:1px solid #d6e1da;border-radius:8px;background:#fff;color:#263c2f;font:inherit;font-weight:400;outline:none}.version-upload textarea:focus,.version-upload input:not([type="file"]):focus{border-color:#70b68c;box-shadow:0 0 0 3px rgba(72,158,106,.1)}.version-file-picker{position:relative;display:grid!important;grid-template-columns:40px minmax(0,1fr) auto;align-items:center;gap:10px!important;min-height:64px;box-sizing:border-box;padding:10px 11px;border:1px dashed #c9d9ce;border-radius:10px;background:#fff;cursor:pointer}.version-file-picker:hover,.version-file-picker:focus-within{border-color:#70b68c;box-shadow:0 0 0 3px rgba(72,158,106,.08)}.version-file-picker.selected{border-style:solid;border-color:#9dcfae;background:#f8fdf9}.version-file-picker>input{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer}.version-file-mark{display:grid;width:38px;height:38px;place-items:center;border-radius:8px;background:#e7f6ec;color:#08783d;font-size:11px;font-weight:800}.version-file-picker>span:nth-of-type(2){display:grid;min-width:0;gap:3px}.version-file-picker>span strong,.version-file-picker>span small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.version-file-picker>span strong{color:#294c36;font-size:12px}.version-file-picker>span small{color:#7e8e84;font-size:11px;font-weight:400}.version-file-picker>b{color:#08783d;font-size:11px;white-space:nowrap}.version-upload-actions{display:flex;justify-content:flex-end;gap:8px}
.version-list{position:relative;display:grid;gap:9px}.version-card{position:relative;display:grid;grid-template-columns:minmax(210px,1fr) auto;align-items:center;gap:12px;padding:13px 14px 13px 29px;border:1px solid #e0e8e2;border-radius:11px;background:#fff}.version-card::before{content:"";position:absolute;left:13px;top:-10px;bottom:-10px;width:1px;background:#dce6df}.version-card:first-child::before{top:50%}.version-card:last-child::before{bottom:50%}.version-marker{position:absolute;left:9px;top:50%;z-index:1;width:9px;height:9px;border:2px solid #fff;border-radius:50%;background:#a7b3ab;box-shadow:0 0 0 1px #cad7ce;transform:translateY(-50%)}.version-card.is-current{border-color:#b9ddc6;background:#fbfefc;box-shadow:inset 3px 0 0 #20a35a}.version-card.is-current .version-marker{background:#18a556;box-shadow:0 0 0 1px #76c893}.version-card.is-pending{border-color:#efd7ab;background:#fffaf1}.version-card.is-pending .version-marker{background:#d6922f;box-shadow:0 0 0 1px #e1bc7f}.version-card-main{min-width:0}.version-identity{display:flex;align-items:center;gap:8px}.version-identity strong{font-size:15px;color:#203b2b}.version-badge{display:inline-flex;align-items:center;padding:3px 7px;border-radius:999px;background:#eff2f0;color:#6e7b73;font-size:11px;font-weight:700}.version-badge.is-current{background:#e5f7eb;color:#07823e}.version-badge.is-approved{background:#edf5ef;color:#447458}.version-badge.is-pending{background:#fff0d4;color:#9a5b07}.version-meta{display:flex;flex-wrap:wrap;gap:7px 16px;margin-top:6px;color:#839087;font-size:11px}.version-actions{display:flex;align-items:center;justify-content:flex-end;gap:6px}.version-action{display:inline-flex;align-items:center;justify-content:center;gap:4px;min-height:30px;box-sizing:border-box;padding:5px 9px;border:1px solid transparent;border-radius:7px;background:transparent;color:#406450;font:inherit;text-decoration:none;cursor:pointer;white-space:nowrap;transition:background .16s ease,border-color .16s ease,color .16s ease}.version-action:hover{border-color:#d8e4dc;background:#f3f8f4;color:#087a3d}.version-action.activate{border-color:#98cfab;background:#edf9f1;color:#08783d;font-weight:700}.version-action.rollback{color:#8d5b35}.version-action.rollback:hover{border-color:#ead9ca;background:#fff8f1;color:#7c431b}.version-action:disabled{cursor:not-allowed;opacity:.5}.version-detail{grid-column:1/-1;margin:1px 0 0;padding-top:13px;border-top:1px solid #e5ece7}.version-detail-heading{display:flex;align-items:baseline;gap:9px;margin-bottom:9px}.version-detail-heading strong{font-size:12px;color:#315741}.version-detail-heading small{color:#8b9890}.version-requirements{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px}.version-requirements article{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:10px;padding:10px 12px;border:1px solid #e1e9e3;border-radius:9px;background:#fff}.version-requirements article>span{padding:3px 7px;border-radius:6px;background:#edf6f0;color:#37704d;font-size:11px;font-weight:700}.version-requirements article>strong{min-width:0;color:#263d2f;font-size:13px;font-weight:600;overflow-wrap:anywhere}.version-requirements article>small{color:#8a978f;font-size:10px;white-space:nowrap}.version-empty-requirements{padding:15px;border:1px dashed #dce5df;border-radius:8px;color:#7d8b82;text-align:center}
.task-versions>details:not(.version-section) pre{max-height:260px;overflow:auto;white-space:pre-wrap;background:#f0f4f1;padding:12px}.task-versions>details:not(.version-section) article{border-top:1px solid #e0e9e3;padding:8px 0}
@media (max-width:720px){.version-section-body{padding:14px}.summary-copy{align-items:flex-start;flex-direction:column;gap:2px}.version-toolbar{align-items:flex-start}.version-card{grid-template-columns:1fr;padding-left:27px}.version-actions{justify-content:flex-start}.version-upload-grid{grid-template-columns:1fr}.version-upload .upload-requirements{grid-column:auto}}
@media (prefers-reduced-motion:reduce){.version-section-summary,.summary-chevron,.version-action{transition:none}}
</style>
