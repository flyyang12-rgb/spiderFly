<script setup>
import { formatTime } from '../../lib/format'
defineProps({ environment: Object, status: String, label: { type: String, default: '运行环境' } })
const stages = { create: '创建环境', install: '安装依赖', interpreter: '解释器检查', pip_check: '依赖一致性检查', requirements: '版本核对' }
</script>
<template>
  <details class="agent-environment">
    <summary>{{ label }} · {{ environment ? (environment.status === 'ready' ? '已校验' : '准备失败') : (status === 'pending' ? '待回传' : '未提供') }}</summary>
    <template v-if="environment">
      <p v-if="environment.python">Python {{ environment.python }} · {{ environment.bits }} 位 · {{ environment.packages.length }} 个包</p>
      <p>{{ environment.cache_reused ? '复用缓存并重新校验' : '首次准备' }} · {{ formatTime(environment.verified_at) }}</p>
      <p v-if="environment.error" class="environment-error">{{ stages[environment.stage] || '环境准备' }}：{{ environment.error }}</p>
      <pre v-if="environment.packages.length" tabindex="0" aria-label="实际安装依赖">{{ environment.packages.map(p => `${p.name}==${p.version}`).join('\n') }}</pre>
      <small>清单 {{ environment.requirements_hash.slice(0, 12) }}</small>
    </template>
    <p v-else>{{ status === 'pending' ? '目标电脑尚未回传校验结果。' : '本次未回传环境明细。旧 Agent 可升级至 0.4.0。' }}</p>
  </details>
</template>
<style scoped>
.agent-environment{min-width:0;margin:12px 0;color:var(--muted,#656d65);font-size:12px;line-height:1.7}summary{width:fit-content;cursor:pointer;color:var(--text,#292b28)}p{margin:6px 0;overflow-wrap:anywhere}.environment-error{color:#a53721}pre{max-height:220px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;padding:8px 12px;border:1px solid var(--border-soft,#dfe3df);border-radius:6px;background:var(--surface-subtle,#f7f8f6);color:var(--text,#292b28);font-size:12px;line-height:1.7}small{font-size:11px}summary:focus-visible,pre:focus-visible{outline:3px solid #00913945;outline-offset:3px}
</style>
