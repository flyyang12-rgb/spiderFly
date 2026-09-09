<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'
const props=defineProps({taskId:{type:Number,required:true}})
const state=ref(null), error=ref(''), busy=ref(false), uploadFile=ref(null), requirements=ref(''), evidence=ref(''), showUpload=ref(false), detail=ref(null)
const names={request:'原始要求',summary:'用途',input:'输入',urls:'站点',scope:'范围',quantity:'数量',fields:'字段',filters:'筛选',sort:'排序',concurrency:'并发',output:'输出',acceptance:'验收'}
const origins={user:'用户要求',inferred:'从代码推断',unspecified:'未说明'}
const labels={pending:'等待验证',testing:'正在验证',repairing:'正在修复',conflict:'需要确认差异',activated:'已启用',failed:'更新失败',cancelled:'已取消',interrupted:'已中断'}
let timer,alive=true
async function api(path,options={}) {
 const r=await fetch('/api/task-versions'+path,{credentials:'include',...options});const value=await r.json()
 if(!r.ok)throw new Error(typeof value.detail==='string'?value.detail:'版本请求失败')
 return value
}
async function load(){try{const value=await api('/tasks/'+props.taskId);if(alive){state.value=value;error.value=''}}catch(e){if(alive)error.value=e.message}}
function openUpload(){requirements.value=state.value?.versions.find(v=>v.id===state.value.active_version_id)?.requirements||'';showUpload.value=!showUpload.value}
async function upload(){
 if(!uploadFile.value)return
 busy.value=true;error.value=''
 try{const form=new FormData();form.append('script',uploadFile.value);form.append('requirements',requirements.value);form.append('evidence',evidence.value);form.append('base_version_id',state.value.active_version_id)
 await api('/tasks/'+props.taskId+'/upload',{method:'POST',body:form});showUpload.value=false;await load()
 }catch(e){error.value=e.message}finally{busy.value=false}
}
async function action(path){busy.value=true;try{await api(path,{method:'POST'});await load()}catch(e){error.value=e.message}finally{busy.value=false}}
async function rollback(id){busy.value=true;error.value='';try{const r=await fetch(`/api/maintenance/tasks/${props.taskId}/rollback/${id}`,{method:'POST',credentials:'include'});const v=await r.json();if(!r.ok)throw new Error(v.detail||'回退失败');await load()}catch(e){error.value=e.message}finally{busy.value=false}}
function formatted(value){return value==null?'未说明':typeof value==='object'?JSON.stringify(value):String(value)}
onMounted(()=>{load();timer=setInterval(load,2500)})
onBeforeUnmount(()=>{alive=false;clearInterval(timer)})
</script>
<template>
 <section class="task-versions" v-if="state">
  <p v-if="error" role="alert">{{error}}</p>
  <details><summary>当前任务要求</summary>
   <dl><template v-for="(item,key) in state.spec" :key="key"><dt>{{names[key]||key}} <small>{{origins[item.origin]}}</small></dt><dd>{{formatted(item.value)}}</dd></template></dl>
  </details>
  <details><summary>代码版本</summary>
   <button class="button ghost" @click="openUpload">更新脚本</button>
   <form v-if="showUpload" @submit.prevent="upload" class="version-upload">
    <label>Python 脚本<input type="file" accept=".py" required @change="uploadFile=$event.target.files[0]"></label>
    <label>Python 依赖<textarea v-model="requirements" rows="2" placeholder="沿用原依赖或填写新依赖"></textarea></label>
    <label>修改说明（可选）<input v-model="evidence" placeholder="例如：修正翻页"></label>
    <button class="button" :disabled="busy">上传并验证</button>
   </form>
   <div class="version-row" v-for="v in state.versions" :key="v.id">
    <span>V{{v.sequence}} {{v.id===state.active_version_id?'· 使用中':v.approved?'· 可回退':'· 未启用'}}</span>
    <a :href="`/api/task-versions/versions/${v.id}/download`">下载 PY</a>
    <button class="button ghost" @click="detail=detail===v.id?null:v.id">详情</button>
    <button class="button ghost" v-if="v.approved&&v.id!==state.active_version_id" :disabled="busy" @click="rollback(v.id)">回退</button>
    <pre v-if="detail===v.id">{{v.spec?JSON.stringify(JSON.parse(v.spec),null,2):'历史版本未整理需求'}}
环境：{{v.runtime||'原任务环境'}}
基于：{{v.base_version_id?'V'+(state.versions.find(item=>item.id===v.base_version_id)?.sequence||'?'):'原始版本'}}</pre>
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
.task-versions{font-size:13px;color:#385f49;margin-top:16px}.task-versions details{margin:12px 0}.task-versions summary{cursor:pointer}.version-row{display:flex;flex-wrap:wrap;gap:12px;align-items:center;padding:8px 0}.version-row span{flex:1}.version-row pre{flex-basis:100%}dl{display:grid;grid-template-columns:150px 1fr;gap:8px}dt small{display:block;color:#77877b}dd{margin:0;overflow-wrap:anywhere;white-space:pre-wrap}.version-upload{display:grid;gap:12px;padding:14px;background:#fff}.version-upload label{display:grid;gap:6px}textarea,input{padding:8px;border:1px solid #dce5df;border-radius:6px}pre{max-height:260px;overflow:auto;white-space:pre-wrap;background:#f0f4f1;padding:12px}article{border-top:1px solid #e0e9e3;padding:8px 0}
</style>
