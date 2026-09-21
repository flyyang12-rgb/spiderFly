<script setup>
import { onMounted } from 'vue'
import { useWorkspaceContext } from '../../workspace/context'

const {
  appForm,
  isAdmin,
  openAi,
  selectRequirementsFile,
  selectScript,
  settings,
  loadTargetHosts,
  targetHosts,
  targetHostsLoading,
  toggleCreateWeekday,
  triggerOptions,
  uploadApp,
  uploadKey,
  uploading,
  view,
  weekdayOptions,
} = useWorkspaceContext()

onMounted(loadTargetHosts)
</script>

<template>
  <section class="view-stack">
    <section class="panel create-ai-entry">
      <div class="create-ai-entry-icon" aria-hidden="true">AI</div>
      <div><strong>让 AI 帮你创建</strong><small>描述需求，AI 会生成脚本和任务配置供你确认</small></div>
      <button class="button primary" type="button" @click="openAi()">开始 AI 创建</button>
    </section>
    <section v-if="isAdmin" class="panel">
      <form class="app-upload-form" @submit.prevent="uploadApp">
        <header class="upload-form-heading">
          <div>
            <h2>上传 Python 创建任务</h2>
            <p>只保存一个 .py 和可选 requirements.txt；运行错误会在日志中报告</p>
          </div>
          <span><b>1</b> 任务与文件</span>
        </header>
        <div class="upload-basics">
          <label class="field"
            ><span>任务名称</span
            ><input v-model="appForm.name" type="text" maxlength="100" placeholder="例如：财务日报"
          /></label>
          <label class="field"
            ><span>任务说明 <small>可选</small></span
            ><input
              v-model="appForm.description"
              type="text"
              maxlength="500"
              placeholder="简单说明这个任务负责什么"
          /></label>
        </div>
        <div class="upload-file-grid">
          <label class="upload-file-card required-file" :class="{ selected: appForm.script }">
            <input :key="uploadKey" type="file" accept=".py,text/x-python" @change="selectScript" />
            <span class="upload-file-mark">PY</span>
            <span class="upload-file-copy"
              ><strong>Python 脚本 <em>必选</em></strong
              ><small>{{ appForm.script?.name || '选择一个 .py 文件' }}</small></span
            >
            <span class="upload-file-action">{{ appForm.script ? '更换' : '选择文件' }}</span>
          </label>
          <label class="upload-file-card" :class="{ selected: appForm.requirements_filename }">
            <input
              :key="'requirements-' + uploadKey"
              type="file"
              accept=".txt,text/plain"
              @change="selectRequirementsFile"
            />
            <span class="upload-file-mark text-file">TXT</span>
            <span class="upload-file-copy"
              ><strong>Python 依赖 <em>可选</em></strong
              ><small>{{ appForm.requirements_filename || '选择 requirements.txt' }}</small></span
            >
            <span class="upload-file-action">{{ appForm.requirements_filename ? '更换' : '选择文件' }}</span>
          </label>
        </div>
        <details class="manual-requirements">
          <summary>
            {{ appForm.requirements_filename ? '已读取依赖，可展开修改' : '没有 requirements.txt？手动填写' }}
          </summary>
          <label class="field">
            <textarea
              v-model="appForm.requirements_text"
              rows="4"
              maxlength="20000"
              placeholder="每行一个，例如：&#10;DrissionPage==4.1.1.4&#10;pandas==2.3.2"
            ></textarea>
          </label>
        </details>
        <p class="field-help">保存前不安装依赖、不运行代码，也不做 Python 语法或业务验证。实际错误由目标宿主机运行后回传。</p>
        <section class="schedule-card create-task-settings">
          <div class="schedule-card-heading">
            <div><strong>运行设置</strong></div>
            <span>北京时间</span>
          </div>
          <div class="trigger-choice-grid">
            <button
              v-for="option in triggerOptions"
              :key="option.value"
              type="button"
              :class="{ active: appForm.trigger_type === option.value }"
              @click="appForm.trigger_type = option.value"
            >
              <i></i>{{ option.label }}
            </button>
          </div>
          <label class="field schedule-target-field">
            <span>计划运行宿主机</span>
            <select v-model="appForm.target_host_id" :disabled="targetHostsLoading">
              <option value="">主控本机（旧执行链路）</option>
              <option
                v-for="host in targetHosts"
                :key="host.id"
                :value="String(host.id)"
                :disabled="host.approval_status !== 'approved'"
              >
                {{ host.name }}{{ host.approval_status === 'approved' ? '' : '（不可用）' }}
              </option>
            </select>
            <small>计划到点时固定发往这台电脑；离线或忙碌时仍保留在该机器队列。</small>
          </label>
          <div v-if="appForm.trigger_type === 'manual'" class="schedule-hint">手动点击“运行”执行</div>
          <label v-else-if="appForm.trigger_type === 'daily'" class="field"
            ><span>每天执行时间</span><input v-model="appForm.daily_time" type="time"
          /></label>
          <div v-else class="weekly-fields">
            <div class="field">
              <span>执行星期</span>
              <div class="weekday-grid">
                <button
                  v-for="day in weekdayOptions"
                  :key="day.value"
                  type="button"
                  :class="{ active: appForm.weekly_days.includes(day.value) }"
                  @click="toggleCreateWeekday(day.value)"
                >
                  周{{ day.label }}
                </button>
              </div>
            </div>
            <label class="field"
              ><span>执行时间</span><input v-model="appForm.weekly_time" type="time"
            /></label>
          </div>
          <div class="create-settings-bottom">
            <div class="field create-plan-state">
              <span>任务状态</span>
              <button
                class="switch-row"
                type="button"
                role="switch"
                :aria-checked="appForm.enabled"
                @click="appForm.enabled = !appForm.enabled"
              >
                <i :class="{ active: appForm.enabled }"><b></b></i
                ><span>{{ appForm.enabled ? '创建后启用' : '暂不启用' }}</span>
              </button>
            </div>
            <div class="notification-options">
              <div><strong>最终通知</strong><small>每次结束通知一次</small></div>
              <label><input v-model="appForm.notify_on_success" type="checkbox" />成功时通知</label>
              <label><input v-model="appForm.notify_on_failure" type="checkbox" />失败时通知</label>
              <label
                :class="{ disabled: !appForm.notify_on_failure }"
                title="失败或超时时尝试截取宿主机前台窗口；错误日志始终保存。"
                ><input
                  v-model="appForm.failure_screenshot"
                  :disabled="!appForm.notify_on_failure"
                  type="checkbox"
                />失败附带截图</label
              >
            </div>
          </div>
        </section>
        <div class="upload-actions">
          <button class="button primary" type="submit" :disabled="uploading">
            {{ uploading ? '正在创建…' : '创建任务' }}
          </button>
        </div>
      </form>
    </section>
  </section>
</template>
