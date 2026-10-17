<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({ title: { type: String, required: true }, closeDisabled: Boolean, wide: Boolean })
const emit = defineEmits(['close'])
const dialog = ref(null)
let previousFocus = null

function keyboard(event) {
  if (event.key === 'Escape') {
    event.stopPropagation()
    if (!props.closeDisabled) emit('close')
  }
  if (event.key !== 'Tab') return
  const controls = [...dialog.value.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]')]
    .filter((element) => element.getClientRects().length)
  const first = controls[0]
  const last = controls.at(-1)
  if (!first) { event.preventDefault(); dialog.value.focus(); return }
  if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.value)) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}

onMounted(async () => {
  previousFocus = document.activeElement
  await nextTick()
  dialog.value?.focus()
})
onBeforeUnmount(() => { if (previousFocus?.isConnected) previousFocus.focus() })
</script>

<template>
  <div class="modal-layer" @mousedown.self="!closeDisabled && emit('close')">
    <section ref="dialog" class="modal workflow-dialog" :class="{ wide }" role="dialog" aria-modal="true" :aria-label="title" tabindex="-1" @keydown="keyboard">
      <header>
        <div><h2>{{ title }}</h2><slot name="subtitle" /></div>
        <button class="modal-close" type="button" aria-label="关闭" :disabled="closeDisabled" @click="emit('close')">×</button>
      </header>
      <div class="modal-body"><slot /></div>
      <footer><slot name="footer"><button class="button secondary" type="button" @click="emit('close')">关闭</button></slot></footer>
    </section>
  </div>
</template>

<style scoped>
.workflow-dialog { width: min(760px, 100%); min-width: 0; max-width: 100%; grid-template-columns: minmax(0, 1fr); }
.workflow-dialog.wide { width: min(940px, 100%); }
.workflow-dialog .modal-body { min-width: 0; grid-template-columns: minmax(0, 1fr); align-content: start; }
.workflow-dialog :deep(p) { line-height: 1.6; }
@media (max-width: 720px) {
  .modal-layer { padding: 12px; }
  .workflow-dialog { max-height: calc(100dvh - 24px); }
  .workflow-dialog > header, .workflow-dialog .modal-body { padding: 16px; }
  .workflow-dialog > footer { padding: 12px 16px; flex-wrap: wrap; }
}
</style>
