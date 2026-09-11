<script setup>
import { useWorkspaceContext } from '../../workspace/context'

const { canChangePassword, changePasswordOpen, logout, me, roleLabel, settings, view } = useWorkspaceContext()
</script>

<template>
  <section class="view-stack settings-grid">
    <section class="panel">
      <header class="panel-heading">
        <div><h2>共享运行方式</h2></div>
        <span class="mini-badge success-badge">{{
          settings.scheduler === 'running' ? '运行中' : '已连接'
        }}</span>
      </header>
      <div class="setting-rows">
        <div><span>执行数量</span><strong>串行执行</strong><small>同时运行 1 个任务</small></div>
        <div>
          <span>调度时区</span><strong>{{ settings.scheduler_timezone || 'Asia/Shanghai' }}</strong
          ><small>北京时间</small>
        </div>
        <div><span>任务环境</span><strong>独立环境</strong></div>
        <div><span>最长运行</span><strong>10 分钟</strong><small>排队时间不计入</small></div>
        <div>
          <span>公共文件夹</span><strong>{{ settings.work_directory_name || '共享工作区' }}</strong
          ><small>运行前后自动清空</small>
        </div>
        <div>
          <span>运行前检查</span><strong>Excel / 端口 {{ settings.managed_browser_port || 9123 }}</strong
          ><small>专用浏览器退出后执行</small>
        </div>
      </div>
    </section>
    <section class="panel">
      <header class="panel-heading">
        <div><h2>我的账号</h2></div>
        <span class="mini-badge neutral-badge">{{ roleLabel(me.role) }}</span>
      </header>
      <div class="profile-block">
        <span class="avatar large-avatar">{{ (me.display_name || me.username).slice(0, 1) }}</span>
        <div>
          <strong>{{ me.display_name || me.username }}</strong
          ><small>@{{ me.username }}</small>
        </div>
      </div>
      <div class="profile-actions">
        <button
          v-if="canChangePassword"
          class="button secondary"
          type="button"
          @click="changePasswordOpen = true"
        >
          修改密码
        </button>
        <small v-else>如需修改密码，请联系超级管理员重置。</small>
        <button class="button ghost" type="button" @click="logout">退出登录</button>
      </div>
    </section>
    <section class="panel">
      <header class="panel-heading">
        <div><h2>飞书通知</h2></div>
        <span class="mini-badge" :class="settings.feishu_configured ? 'success-badge' : 'warning-badge'">{{
          settings.feishu_configured ? '已配置' : '待配置'
        }}</span>
      </header>
      <div class="policy-list">
        <div>
          <span class="policy-index success-bg">01</span>
          <p><strong>运行成功</strong><small>任务、状态、耗时</small></p>
        </div>
        <div>
          <span class="policy-index danger-bg">02</span>
          <p><strong>运行失败</strong><small>错误摘要与结果</small></p>
        </div>
        <div>
          <span class="policy-index neutral-bg">03</span>
          <p><strong>安静通知</strong><small>仅在结束时通知</small></p>
        </div>
      </div>
    </section>
  </section>
</template>
