<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'

const props = defineProps({ threads: { type: Array, default: () => [] }, selectedId: Number, currentTitle: String, disabled: Boolean })
const emit = defineEmits(['select', 'delete'])
const root = ref(null)
const trigger = ref(null)
const menu = ref(null)
const expanded = ref(false)
const focusedIndex = ref(0)
const deletingIndex = ref(null)
const menuId = useId()

function close(restoreFocus = false) {
  expanded.value = false
  if (restoreFocus) trigger.value?.focus()
}
async function focusOption(index) {
  focusedIndex.value = Math.max(0, Math.min(props.threads.length - 1, index))
  await nextTick()
  menu.value?.querySelectorAll('.thread-option')[focusedIndex.value]?.focus()
}
async function open() {
  if (props.disabled || !props.threads.length) return
  expanded.value = true
  await focusOption(props.threads.findIndex(item => item.id === props.selectedId))
}
function select(id) { if (props.disabled) return; close(true); emit('select', id) }
function remove(id, index) {
  if (props.disabled) return
  deletingIndex.value = index
  menu.value?.focus({ preventScroll: true })
  emit('delete', id)
}
function keydown(event) {
  if (event.key === 'Escape' && expanded.value) {
    event.preventDefault(); event.stopPropagation(); close(true)
  } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
    event.preventDefault()
    if (!expanded.value) { open(); return }
    const index = event.key === 'Home' ? 0 : event.key === 'End' ? props.threads.length - 1 : focusedIndex.value + (event.key === 'ArrowDown' ? 1 : -1)
    focusOption(index)
  }
}
function outside(event) { if (!root.value?.contains(event.target)) close() }
function description(item) {
  const date = new Date(item.updated_at || item.created_at)
  const time = Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }) + ' 更新'
  return [item.task_id ? '任务对话' : '任务创建', time].filter(Boolean).join(' · ')
}
watch(() => props.disabled, async value => {
  if (deletingIndex.value === null) { if (value) close(); return }
  if (value) return
  const index = deletingIndex.value
  deletingIndex.value = null
  await nextTick()
  if (expanded.value) {
    const buttons = menu.value?.querySelectorAll('.thread-delete')
    buttons?.[Math.min(index, buttons.length - 1)]?.focus({ preventScroll: true })
  }
})
onMounted(() => document.addEventListener('pointerdown', outside))
onBeforeUnmount(() => document.removeEventListener('pointerdown', outside))
</script>

<template>
  <div ref="root" class="thread-picker" @keydown="keydown" @focusout="event => { if (!root?.contains(event.relatedTarget)) close() }">
    <button ref="trigger" type="button" class="thread-trigger" :class="{ expanded }" aria-label="切换对话" aria-haspopup="dialog" :aria-controls="menuId" :aria-expanded="expanded" :disabled="disabled || !threads.length" @click="expanded ? close() : open()">
      <svg class="thread-icon" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M5 4h10a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H8l-5 3V6a2 2 0 0 1 2-2Z"/><path d="M7 8h6M7 11h4"/></svg>
      <span class="thread-title">{{ currentTitle || '选择对话' }}</span>
      <svg class="thread-chevron" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m4 6 4 4 4-4"/></svg>
    </button>
    <div v-if="expanded" class="thread-popover" role="dialog" aria-label="对话历史">
      <div class="thread-menu-heading"><span>切换对话</span><span>{{ threads.length }} 个对话</span></div>
      <div :id="menuId" ref="menu" tabindex="-1" class="thread-options" role="list" aria-label="已有对话">
        <div v-for="(item, index) in threads" :key="item.id" class="thread-row" role="listitem" :class="{ selected: item.id === selectedId }">
        <button type="button" class="thread-option" :aria-current="item.id === selectedId ? 'true' : undefined" :tabindex="index === focusedIndex ? 0 : -1" @focus="focusedIndex = index" @click="select(item.id)">
          <span class="thread-option-copy"><span>{{ item.id === selectedId ? currentTitle || item.title : item.title }}</span><small>{{ description(item) }}</small></span>
          <svg v-if="item.id === selectedId" class="thread-check" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m3 8 3 3 7-7"/></svg>
        </button>
        <button type="button" class="thread-delete" :aria-label="`删除对话：${item.title}`" :disabled="disabled" @click.stop="remove(item.id, index)">
          <svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 4h10M6 4V2h4v2M4 4l.5 10h7L12 4M6.5 7v4M9.5 7v4"/></svg>
        </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.thread-picker { position: relative; min-width: 0; font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
.thread-trigger { display: flex; align-items: center; gap: 8px; width: 100%; height: 38px; padding: 0 12px; border: 1px solid #DFE3DF; border-radius: 8px; background: #fff; color: #292B28; font-size: 13px; text-align: left; cursor: pointer; }
.thread-trigger:hover:not(:disabled) { background: #F7F8F6; }
.thread-trigger.expanded { border-color: #009139; }
.thread-trigger:focus-visible, .thread-option:focus-visible { outline: 2px solid #009139; outline-offset: 2px; }
.thread-trigger:disabled { opacity: .5; cursor: not-allowed; }
.thread-title { flex: 1; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
svg { flex-shrink: 0; width: 16px; height: 16px; stroke: currentColor; stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; }
.thread-icon { width: 18px; height: 18px; color: #757A75; }
.thread-chevron { color: #757A75; transition: transform 140ms ease; }
.expanded .thread-chevron { transform: rotate(180deg); }
.thread-popover { position: absolute; top: calc(100% + 6px); left: 0; width: 100%; min-width: min(280px, calc(100vw - 64px)); z-index: 5; padding: 4px; border: 1px solid #DFE3DF; border-radius: 10px; background: #fff; box-shadow: 0 8px 24px #2218141a; }
.thread-menu-heading { display: flex; justify-content: space-between; gap: 8px; padding: 8px; color: #757A75; font-size: 11px; line-height: 16px; border-bottom: 1px solid #ECEFEC; }
.thread-options { max-height: min(320px, 40dvh); overflow-y: auto; overscroll-behavior: contain; padding: 4px; scrollbar-width: thin; }
.thread-option { min-width: 0; flex: 1; display: flex; align-items: center; gap: 12px; width: 100%; padding: 12px 8px; border: 0; border-radius: 6px; background: #fff; color: #292B28; text-align: left; cursor: pointer; }
.thread-option:hover { background: #F7F8F6; }
.thread-row.selected, .thread-row.selected .thread-option { background: #edf7f0; color: #006b2a; }
.thread-option-copy { display: grid; gap: 4px; min-width: 0; flex: 1; font-size: 12px; line-height: 20px; overflow-wrap: anywhere; }
.thread-option-copy small { color: #757A75; font-size: 11px; line-height: 16px; }
.thread-row { display: flex; align-items: center; border-radius: 6px; }
.thread-delete { display: grid; place-items: center; flex-shrink: 0; width: 32px; height: 32px; margin: 0 4px; padding: 0; border: 0; border-radius: 6px; background: transparent; color: #757A75; cursor: pointer; }
.thread-delete:hover { background: #fff0ec; color: #a83e29; }
.thread-delete:focus-visible { outline: 2px solid #009139; outline-offset: 1px; }
.thread-delete:disabled { opacity: .5; cursor: not-allowed; }
.thread-check { color: #009139; }
@media (prefers-reduced-motion: reduce) { .thread-chevron { transition: none; } }
</style>
