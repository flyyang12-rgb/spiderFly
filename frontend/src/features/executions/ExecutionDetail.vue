<script setup>
import { maintenanceResult } from '../../maintenanceSummary'
import { useWorkspaceContext } from '../../workspace/context'
import MaintenanceLog from '../../MaintenanceLog.vue'
import CollectionProgress from './CollectionProgress.vue'

const {
  artifactDownload,
  artifactFiles,
  businessOutcomeLabel,
  cancelExecution,
  detail,
  detailFinished,
  downloadArtifact,
  formatDuration,
  formatFileSize,
  isAdmin,
  notificationLabel,
  openExecution,
  requesterName,
  statusLabel,
  stoppingBusy,
  stoppingExecution,
} = useWorkspaceContext()
</script>

<template>
  <div class="modal-layer" @mousedown.self="detail = null">
    <section class="modal log-modal" role="dialog" aria-modal="true" aria-label="执行日志">
      <header>
        <div>
          <span class="eyebrow">EXECUTION #{{ detail.id }}</span>
          <h2>{{ detail.task_name }}</h2>
        </div>
        <button class="modal-close" type="button" aria-label="关闭" @click="detail = null">×</button>
      </header>
      <div class="execution-summary">
        <span class="status-badge"
          ><i class="status-dot" :class="detail.status"></i>{{ statusLabel(detail.status) }}</span
        >
        <span v-if="detail.status === 'pending'"
          >队列 <strong>第 {{ detail.queue_position || '—' }} 位</strong></span
        >
        <span
          >发起人 <strong>{{ requesterName(detail) }}</strong></span
        >
        <span
          >耗时 <strong>{{ formatDuration(detail.duration_ms) }}</strong></span
        >
        <span
          >退出码 <code>{{ detail.exit_code == null ? '—' : detail.exit_code }}</code></span
        >
        <span v-if="detail.business_outcome"
          >脚本结果 <strong>{{ businessOutcomeLabel(detail.business_outcome) }}</strong></span
        >
        <span
          >飞书 <strong>{{ notificationLabel(detail.notification_status) }}</strong></span
        >
      </div>
      <div class="modal-body log-body">
        <CollectionProgress :progress="detail.collection_progress" :status="detail.status" />
        <div
          v-if="detail.maintenance"
          class="notice execution-maintenance"
          :class="
            detail.maintenance.status === 'activated' &&
            !['failed', 'timeout', 'cancelled'].includes(detail.maintenance.rerun_status)
              ? 'info'
              : 'warning'
          "
        >
          <span class="notice-icon">{{ detail.maintenance.rerun_status === 'success' ? '✓' : 'i' }}</span>
          <div class="execution-maintenance-content">
            <strong>{{
              detail.maintenance.status === 'activated'
                ? maintenanceResult(detail.maintenance) + ' · V' + detail.maintenance.version
                : detail.maintenance.ended_at
                  ? '维护结果'
                  : '正在自动修复'
            }}</strong>
            <MaintenanceLog
              compact
              :job="detail.maintenance"
              :execution-id="detail.id"
              @open-execution="(id) => openExecution({ id })"
            />
          </div>
        </div>
        <div v-if="detail.status === 'pending' && detail.error_message" class="notice info">
          <span class="notice-icon">i</span>
          <div>
            <strong>仍在排队，没有开始计时</strong><small>{{ detail.error_message }}</small>
          </div>
        </div>
        <div
          v-if="detail.result_source === 'result_json'"
          class="notice"
          :class="detail.business_outcome === 'success' ? 'info' : 'warning'"
        >
          <span class="notice-icon">{{ detail.business_outcome === 'success' ? '✓' : '!' }}</span>
          <div>
            <strong
              >{{ businessOutcomeLabel(detail.business_outcome) }} ·
              {{ detail.result_code || '未提供编码' }}</strong
            >
            <small>{{ detail.result_message || '脚本未提供结果说明' }}</small>
            <small v-if="detail.retryable != null">建议重试：{{ detail.retryable ? '是' : '否' }}</small>
            <small v-if="detail.manual_code">人工处理编码：{{ detail.manual_code }}</small>
            <small v-if="detail.manual_action_url"
              ><a :href="detail.manual_action_url" target="_blank" rel="noopener noreferrer"
                >打开人工处理链接</a
              ></small
            >
          </div>
        </div>
        <div class="execution-artifacts">
          <div class="artifact-heading">
            <span class="log-label">本次文件</span
            ><small v-if="detailFinished && artifactFiles.length">{{ artifactFiles.length }} 个文件</small>
          </div>
          <p v-if="!detailFinished" class="artifact-empty">运行结束后可下载</p>
          <template v-else-if="detail.artifacts">
            <ul v-if="artifactFiles.length" class="artifact-list">
              <li v-for="file in artifactFiles" :key="file.path" class="artifact-row">
                <div class="artifact-info">
                  <span class="artifact-path">{{ file.path }}</span
                  ><small>{{ formatFileSize(file.size_bytes) }}</small>
                </div>
                <button
                  class="button secondary compact"
                  type="button"
                  :disabled="artifactDownload.busy"
                  :aria-label="'下载 ' + file.path"
                  @click="downloadArtifact(file)"
                >
                  {{ artifactDownload.busy && artifactDownload.path === file.path ? '准备下载…' : '下载' }}
                </button>
              </li>
            </ul>
            <p v-else-if="!detail.artifacts.error" class="artifact-empty">暂无文件</p>
            <p v-if="detail.artifacts.error" class="artifact-message danger-text">文件读取失败，请刷新</p>
            <p v-if="detail.artifacts.truncated" class="artifact-message">仅显示部分文件</p>
          </template>
          <p v-else class="artifact-empty">正在读取本次文件…</p>
          <p
            v-if="artifactDownload.message"
            class="artifact-message"
            :class="{ 'danger-text': artifactDownload.error }"
            role="status"
          >
            {{ artifactDownload.message }}
          </p>
        </div>
        <div>
          <span class="log-label">标准输出 stdout</span>
          <pre>{{
            detail.stdout || (['pending', 'running'].includes(detail.status) ? '等待程序输出…' : '（无输出）')
          }}</pre>
        </div>
        <div v-if="detail.stderr || (detail.error_message && detail.status !== 'pending')">
          <span class="log-label danger-text">错误输出 stderr</span>
          <pre class="error-log">{{ detail.stderr || detail.error_message }}</pre>
        </div>
        <div v-if="detail.notification_error" class="notice warning">
          <span class="notice-icon">!</span>
          <div>
            <strong>飞书通知未发送</strong><small>{{ detail.notification_error }}</small>
          </div>
        </div>
      </div>
      <footer>
        <button
          v-if="detail.status === 'pending'"
          class="button danger"
          type="button"
          @click="cancelExecution(detail)"
        >
          取消排队
        </button>
        <button
          v-if="detail.status === 'running' && isAdmin"
          class="button danger"
          type="button"
          :disabled="detail.stop_requested || stoppingBusy"
          @click="stoppingExecution = { ...detail }"
        >
          {{ detail.stop_requested ? '正在停止…' : '强制停止' }}
        </button>
        <button class="button secondary" type="button" @click="detail = null">关闭日志</button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.execution-maintenance{align-items:flex-start;padding:12px 14px;gap:10px}
.execution-maintenance .notice-icon{margin-top:1px}
.execution-maintenance-content{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 18px;min-width:0;flex:1}
.execution-maintenance-content>strong{font-size:13px;line-height:1.6}
.execution-maintenance-content>.maintenance-log{flex:1;min-width:180px}
</style>
