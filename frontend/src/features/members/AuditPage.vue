<script setup>
import { useWorkspaceContext } from '../../workspace/context'
import SystemSettingsPage from '../settings/SystemSettingsPage.vue'
import { useAuditHistory } from './useAuditHistory'

const { auditActionLabel, auditActor, auditTarget, formatTime, me, isAdmin, handleSessionExpired } =
  useWorkspaceContext()
const { items: auditLogs, total, page, pageSize, totalPages, loading, error, load } =
  useAuditHistory({ me, isAdmin, handleSessionExpired })
</script>

<template>
  <section class="view-stack">
    <SystemSettingsPage />
    <section class="panel table-panel">
      <header class="panel-heading">
        <div class="audit-title"><h2>操作记录</h2><span role="status">{{ loading ? '加载中…' : '共 ' + total + ' 条' }}</span></div>
        <button
          class="button secondary compact"
          type="button"
          :disabled="loading"
          @click="load()"
        >
          刷新
        </button>
      </header>
      <div v-if="error" class="audit-error" role="alert">{{ error }}{{ auditLogs.length ? '；保留上次记录。' : '' }}<button class="text-button" type="button" :disabled="loading" @click="load()">重试</button></div>
      <div v-if="auditLogs.length" class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>操作人</th>
              <th>操作</th>
              <th>对象</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in auditLogs" :key="item.id">
              <td>{{ formatTime(item.created_at) }}</td>
              <td>{{ auditActor(item) }}</td>
              <td>
                <strong class="audit-action">{{ auditActionLabel(item.action) }}</strong>
              </td>
              <td>{{ auditTarget(item) }}</td>
              <td class="audit-detail">
                {{ item.summary || item.detail || item.description || item.message || '—' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="empty-state compact-empty"><strong>{{ loading ? '正在读取操作记录…' : error ? '暂时无法读取记录' : '暂无操作记录' }}</strong></div>
      <footer v-if="total" class="pagination-bar">
        <span>第 {{ (page - 1) * pageSize + 1 }}–{{ Math.min(page * pageSize, total) }} 条，共 {{ total }} 条</span>
        <div>
          <button class="button secondary compact" type="button" :disabled="loading || page <= 1" @click="load(page - 1)">上一页</button>
          <strong>{{ page }} / {{ totalPages }}</strong>
          <button class="button secondary compact" type="button" :disabled="loading || page >= totalPages" @click="load(page + 1)">下一页</button>
        </div>
      </footer>
    </section>
  </section>
</template>

<style scoped>
.audit-title { display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px; }
.audit-title > span { color: var(--muted, #757a75); font-size: 12px; }
.audit-detail { min-width: 160px; max-width: 480px; white-space: normal; overflow-wrap: anywhere; line-height: 1.7; }
.audit-error { margin: 0 18px 16px; color: var(--coral, #ad4935); overflow-wrap: anywhere; }
.audit-error > button { margin-left: 8px; }
.view-stack, .table-panel, .table-wrap { min-width: 0; max-width: 100%; }
@media (max-width: 720px) { .pagination-bar { flex-wrap: wrap; gap: 12px; } }
</style>
