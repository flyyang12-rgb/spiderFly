<script setup>
import { useWorkspaceContext } from '../../workspace/context'

const { auditActionLabel, auditActor, auditLogs, auditTarget, detail, formatTime, loadAll, view } =
  useWorkspaceContext()
</script>

<template>
  <section class="view-stack">
    <section class="panel table-panel">
      <header class="panel-heading">
        <div>
          <h2>最近操作</h2>
          <p>最近 {{ auditLogs.length }} 条审计记录</p>
        </div>
        <button
          class="button secondary compact"
          type="button"
          @click="loadAll({ quiet: true, includeAdmin: true })"
        >
          刷新记录
        </button>
      </header>
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
              <td>
                <span class="person-chip">{{ auditActor(item).slice(0, 1) }}</span
                >{{ auditActor(item) }}
              </td>
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
      <div v-else class="empty-state compact-empty"><strong>暂无审计记录</strong></div>
    </section>
  </section>
</template>
