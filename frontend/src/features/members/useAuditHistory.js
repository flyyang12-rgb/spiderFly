import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { request } from '../../lib/api'

export function useAuditHistory({ me, isAdmin, handleSessionExpired }) {
  const items = ref([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = 10
  const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
  const loading = ref(false)
  const error = ref('')
  let generation = 0
  let controller
  let disposed = false

  function invalidate() {
    generation += 1
    controller?.abort()
    loading.value = false
  }

  async function load(targetPage = page.value) {
    if (disposed || !me.value || !isAdmin.value) return
    invalidate()
    const owner = me.value
    const currentGeneration = generation
    controller = new AbortController()
    const current = () => !disposed && me.value === owner && isAdmin.value && generation === currentGeneration
    loading.value = true
    error.value = ''
    try {
      const result = await request(`/audit-logs/history?page=${targetPage}&page_size=${pageSize}`, { signal: controller.signal })
      if (!current()) return
      items.value = result.items
      total.value = result.total
      page.value = result.page
    } catch (cause) {
      if (!current() || cause.name === 'AbortError') return
      if (cause.status === 401) handleSessionExpired()
      else error.value = cause.message || '读取操作记录失败'
    } finally {
      if (current()) loading.value = false
    }
  }

  watch([me, isAdmin], () => {
    invalidate()
    items.value = []
    total.value = 0
    page.value = 1
    error.value = ''
    if (me.value && isAdmin.value) load(1)
  }, { immediate: true, flush: 'sync' })
  onBeforeUnmount(() => { disposed = true; invalidate() })

  return { items, total, page, pageSize, totalPages, loading, error, load }
}
