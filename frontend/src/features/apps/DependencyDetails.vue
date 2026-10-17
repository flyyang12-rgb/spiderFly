<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'
import { request } from '../../lib/api'
import EnvironmentDetails from '../hosts/EnvironmentDetails.vue'
const props = defineProps({ appId: Number, remote: Boolean })
const data=ref(null), error=ref('')
let sequence=0, controller, alive=true
async function load(event) {
  if (!event.target.open || !props.appId) return
  const own=++sequence; controller?.abort(); controller=new AbortController(); error.value=''
  try { const value=await request(`/apps/${props.appId}/dependency-details`,{signal:controller.signal}); if(alive&&own===sequence)data.value=value }
  catch(e){if(alive&&own===sequence&&e.name!=='AbortError')error.value=e.message}
}
watch(()=>props.appId,()=>{sequence++;controller?.abort();data.value=null})
onBeforeUnmount(()=>{alive=false;sequence++;controller?.abort()})
</script>
<template>
 <details class="dependency-details" @toggle="load"><summary>依赖与环境</summary>
  <p v-if="error" role="alert">{{error}}</p>
  <p v-if="remote">目标环境在领取运行后准备，记录以每次运行回传为准。</p>
  <template v-if="data">
   <details v-for="v in data.versions" :key="v.id"><summary>v{{v.sequence}} · {{v.analysis?'冻结依赖':'历史版本，无分析记录'}}</summary>
    <template v-if="v.analysis"><pre>{{v.analysis.requirements||'仅标准库'}}</pre><p v-for="item in v.analysis.exclusions" :key="item.id">{{item.id}}：{{item.reason}}</p><small>{{v.analysis.parser_version}} · {{v.analysis.mapping_version}}</small></template>
   </details>
   <details v-for="(env,index) in data.environments" :key="index"><summary>主控环境 · {{env.version_sequence ? `v${env.version_sequence}` : '历史版本'}} · {{env.status==='ready'?'已就绪':'安装失败'}} · {{env.completed_at}}</summary>
    <p v-if="env.evidence.error">{{env.evidence.error}}</p>
    <template v-else><p>Python {{env.evidence.python}} · {{env.evidence.bits}} 位</p><pre>{{env.evidence.packages.map(p=>`${p.name}==${p.version}`).join('\n')}}</pre></template>
   </details>
   <p v-if="!data.environments.length">尚无本机环境记录</p>
   <EnvironmentDetails v-for="record in data.remote_environments || []" :key="record.run_id" :environment="record.environment" :label="`${record.host_name} · v${record.version_sequence || '?'} · 运行 #${record.run_id}`" />
  </template>
 </details>
</template>
<style scoped>.dependency-details{font-size:12px;margin:12px 0;color:var(--muted)}summary{cursor:pointer}details details{margin:8px 0}pre{color:var(--text);background:var(--surface-subtle);border:1px solid var(--border-soft);padding:8px;border-radius:6px;font-size:12px;line-height:1.7;white-space:pre-wrap;overflow-wrap:anywhere;max-height:220px;overflow:auto}p{overflow-wrap:anywhere}small{font-size:11px}</style>
