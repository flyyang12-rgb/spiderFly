<script setup>
import { computed } from 'vue'
import { renderAiMarkdown } from './lib/aiMarkdown.js'

const props = defineProps({ content: { type: String, default: '' } })
const html = computed(() => renderAiMarkdown(props.content))
</script>

<template>
  <div class="ai-message-content" v-html="html"></div>
</template>

<style scoped>
.ai-message-content{font-size:15px;line-height:1.75;color:var(--text,#292b28);overflow-wrap:anywhere;min-width:0}
.ai-message-content :deep(> :first-child){margin-top:0}.ai-message-content :deep(> :last-child){margin-bottom:0}
.ai-message-content :deep(p){margin:0 0 12px}.ai-message-content :deep(ul),.ai-message-content :deep(ol){margin:8px 0 16px;padding-left:24px}.ai-message-content :deep(li){margin:4px 0}.ai-message-content :deep(li>p){margin:4px 0}
.ai-message-content :deep(h1),.ai-message-content :deep(h2),.ai-message-content :deep(h3),.ai-message-content :deep(h4),.ai-message-content :deep(h5),.ai-message-content :deep(h6){margin:24px 0 10px;font-size:16px;line-height:1.5;font-weight:600;color:var(--ink,#221814)}
.ai-message-content :deep(strong){font-weight:600}.ai-message-content :deep(a){color:var(--brand-strong,#00752e);text-decoration:underline;text-underline-offset:3px}.ai-message-content :deep(a:focus-visible){outline:2px solid var(--brand,#009139);outline-offset:3px}
.ai-message-content :deep(code){font-family:Consolas,monospace;font-size:.88em;padding:2px 5px;border-radius:4px;background:var(--surface-subtle,#f7f8f6)}
.ai-message-content :deep(pre){max-width:100%;overflow:auto;margin:16px 0;padding:16px;border:1px solid var(--border-soft,#ecefec);border-radius:8px;background:#f7f8f6;line-height:1.65;white-space:pre;tab-size:4}.ai-message-content :deep(pre code){padding:0;background:none;font-size:13px}
.ai-message-content :deep(blockquote){margin:16px 0;padding:0 0 0 16px;border-left:2px solid var(--border,#dfe3df);color:#59645c}.ai-message-content :deep(hr){margin:24px 0;border:0;border-top:1px solid var(--border-soft,#ecefec)}
.ai-message-content :deep(table){display:block;width:max-content;max-width:100%;overflow:auto;margin:16px 0;border-collapse:collapse;font-size:13px}.ai-message-content :deep(th),.ai-message-content :deep(td){padding:8px 12px;border:1px solid var(--border,#dfe3df);text-align:left}.ai-message-content :deep(th){font-weight:600;background:var(--surface-subtle,#f7f8f6)}
</style>
