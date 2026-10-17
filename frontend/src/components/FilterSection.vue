<script setup>
import { inject, onBeforeUnmount, onMounted, ref, useId } from 'vue'

defineProps({ active: Boolean })
const regionId = useId()
const mobile = ref(false)
const expanded = inject('spiderfly-history-filters-expanded', ref(false))
let media
const updateViewport = event => { mobile.value = event.matches }

onMounted(() => {
  if (typeof window.matchMedia !== 'function') return
  media = window.matchMedia('(max-width: 720px)')
  updateViewport(media)
  if (media.addEventListener) media.addEventListener('change', updateViewport)
  else media.addListener?.(updateViewport)
})
onBeforeUnmount(() => {
  if (media?.removeEventListener) media.removeEventListener('change', updateViewport)
  else media?.removeListener?.(updateViewport)
})
</script>

<template>
  <div class="list-filter-section">
    <div v-if="mobile" class="list-filter-toggle">
      <button type="button" class="button secondary compact" :aria-expanded="expanded" :aria-controls="regionId" @click="expanded = !expanded">
        {{ expanded ? '收起筛选' : '筛选' }}<span v-if="active"> · 已设置</span>
      </button>
    </div>
    <div :id="regionId" :hidden="mobile && !expanded"><slot /></div>
  </div>
</template>

<style scoped>
.list-filter-section { min-width: 0; border-bottom: 1px solid var(--border-soft); }
.list-filter-toggle { padding: 12px 18px; }
[hidden] { display: none; }
</style>
