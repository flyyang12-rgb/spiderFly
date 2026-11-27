<script setup>
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
defineOptions({ inheritAttrs: false })
const props = defineProps({ modelValue: { default: '' }, options: { type: Array, default: () => [] },
  label: String, placeholder: { type: String, default: '请选择' }, disabled: Boolean, required: Boolean })
const emit = defineEmits(['update:modelValue'])
const root = ref(null), trigger = ref(null), menu = ref(null), open = ref(false), current = ref(-1)
const id = useId()
const upwards = ref(false), available = ref(240)
const selected = computed(() => props.options.find(option => String(option.value) === String(props.modelValue)))
const enabled = computed(() => props.options.map((option, index) => option.disabled ? -1 : index).filter(index => index >= 0))
let search = '', timer
function close(restore = false) { open.value = false; if (restore) trigger.value?.focus() }
async function show() {
  if (props.disabled) return
  const rect = root.value.getBoundingClientRect()
  const boundary = root.value.closest('.modal-body')?.getBoundingClientRect()
  const below = Math.min(innerHeight, boundary?.bottom ?? innerHeight) - rect.bottom - 12
  const above = rect.top - Math.max(0, boundary?.top ?? 0) - 12
  upwards.value = below < Math.min(240, props.options.length * 40 + 12) && above > below
  available.value = Math.max(80, Math.min(240, upwards.value ? above : below))
  open.value = true
  current.value = props.options.indexOf(selected.value)
  if (!enabled.value.includes(current.value)) current.value = enabled.value[0] ?? -1
  await nextTick(); menu.value?.focus(); scroll()
}
function scroll() { menu.value?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest' }) }
function choose(index) {
  const option = props.options[index]
  if (!option || option.disabled) return
  emit('update:modelValue', option.value); close(true)
}
function keydown(event) {
  if (event.key === 'Tab') { close(); return }
  if (event.key === 'Escape') { if (open.value) { event.preventDefault(); event.stopPropagation(); close(true) }; return }
  if (['Enter', ' '].includes(event.key)) { event.preventDefault(); if (open.value) choose(current.value); else show(); return }
  if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
    event.preventDefault()
    if (!open.value) { show(); return }
    const list = enabled.value, index = list.indexOf(current.value)
    current.value = event.key === 'Home' ? list[0] : event.key === 'End' ? list.at(-1) : list[(index + (event.key === 'ArrowDown' ? 1 : -1) + list.length) % list.length]
    nextTick(scroll); return
  }
  if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    search += event.key.toLowerCase(); clearTimeout(timer); timer = setTimeout(() => { search = '' }, 600)
    const index = props.options.findIndex(option => !option.disabled && option.label.toLowerCase().startsWith(search))
    if (index >= 0) { current.value = index; if (!open.value) show(); nextTick(scroll) }
  }
}
watch(() => props.disabled, value => { if (value) close() })
onBeforeUnmount(() => clearTimeout(timer))
</script>
<template>
  <div ref="root" class="ui-select" @focusout="event => { if (!root.contains(event.relatedTarget)) close() }" @keydown="keydown">
    <select class="sr-only" tabindex="-1" aria-hidden="true" :value="modelValue" :required="required" :disabled="disabled" @invalid.prevent="show()" @change="emit('update:modelValue', $event.target.value)">
      <option v-if="!selected" value="">{{ placeholder }}</option>
      <option v-for="option in options" :key="option.value" :value="option.value" :disabled="option.disabled">{{ option.label }}</option>
    </select>
    <button ref="trigger" class="ui-select-trigger" type="button" role="combobox" :aria-label="label" aria-haspopup="listbox" :aria-expanded="open" :aria-controls="id" :aria-required="required" :disabled="disabled" @click="open ? close() : show()"><span :class="{ placeholder: !selected }">{{ selected?.label || placeholder }}</span><i aria-hidden="true"></i></button>
    <div v-if="open" class="ui-select-dismiss" @mousedown.prevent="close()"></div>
    <div v-if="open" :id="id" ref="menu" class="ui-select-menu" :class="{ upwards }" :style="{maxHeight: available + 'px'}" role="listbox" :aria-label="label" :aria-activedescendant="current >= 0 ? id + '-' + current : undefined" tabindex="-1">
      <div v-for="(option, index) in options" :id="id + '-' + index" :key="option.value" class="ui-select-option" role="option" :aria-selected="String(option.value) === String(modelValue)" :aria-disabled="Boolean(option.disabled)" :data-active="index === current" @mousemove="!option.disabled && (current = index)" @mousedown.prevent="choose(index)" @click.prevent.stop><span>{{ option.label }}</span><b v-if="String(option.value) === String(modelValue)" aria-hidden="true">✓</b></div>
      <p v-if="!options.length">暂无选项</p>
    </div>
  </div>
</template>
<style scoped>
.ui-select{position:relative;min-width:0;width:100%}
.ui-select-trigger{display:flex;align-items:center;justify-content:space-between;gap:14px;width:100%;min-height:42px;padding:9px 12px;border:1px solid var(--border);border-radius:8px;background:#fff;color:var(--text);font:inherit;font-size:15px;text-align:left;cursor:pointer}
.ui-select-trigger>span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.ui-select-trigger .placeholder{color:var(--muted)}
.ui-select-trigger i{width:7px;height:7px;flex:none;border-right:1.5px solid var(--muted);border-bottom:1.5px solid var(--muted);transform:translateY(-2px) rotate(45deg)}
.ui-select-trigger:hover:not(:disabled){border-color:#b6c7bb}.ui-select-trigger[aria-expanded="true"]{border-color:var(--brand);box-shadow:0 0 0 2px #00913912}.ui-select-trigger:disabled{background:var(--surface-subtle);color:var(--muted);cursor:not-allowed}
.ui-select-dismiss{position:fixed;inset:0;z-index:4}
.ui-select-menu{position:absolute;z-index:5;top:calc(100% + 6px);left:0;right:0;max-height:min(240px,40dvh);overflow:auto;padding:5px;border:1px solid var(--border);border-radius:9px;background:#fff;box-shadow:0 8px 24px #1b372317;outline:none}
.ui-select-menu.upwards{top:auto;bottom:calc(100% + 6px)}
.ui-select-option{display:flex;justify-content:space-between;gap:12px;min-height:38px;padding:8px 10px;border-radius:6px;font-size:15px;line-height:1.5;cursor:pointer}.ui-select-option>span{min-width:0;overflow-wrap:anywhere}
.ui-select-option[data-active="true"]{background:var(--surface-subtle)}.ui-select-option[aria-selected="true"]{color:var(--brand-strong);background:var(--brand-soft)}.ui-select-option[aria-disabled="true"]{color:var(--faint);cursor:not-allowed}.ui-select-option b{font-weight:500}
.ui-select-menu p{padding:8px 10px;margin:0;color:var(--muted);font-size:13px}
</style>
