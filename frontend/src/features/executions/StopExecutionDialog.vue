<script setup>
import { useWorkspaceContext } from '../../workspace/context'

const { forceStopExecution, stoppingBusy, stoppingExecution } = useWorkspaceContext()
</script>

<template>
  <div class="modal-layer" @mousedown.self="!stoppingBusy && (stoppingExecution = null)">
    <section class="modal confirm-modal" role="alertdialog" aria-modal="true" aria-label="强制停止任务">
      <header>
        <h2>强制停止“{{ stoppingExecution.task_name }}”</h2>
        <button
          class="modal-close"
          type="button"
          aria-label="关闭"
          :disabled="stoppingBusy"
          @click="stoppingExecution = null"
        >
          ×
        </button>
      </header>
      <div class="modal-body confirm-copy">
        <p>将停止本次程序及子进程。未保存内容可能丢失，已完成的操作无法撤回。</p>
        <p v-if="stoppingExecution.trigger_source === 'workflow'">停止后将结束本轮工作流，并取消后续步骤。</p>
        <p v-else>停止后保留日志，清理完成后继续下一项任务。</p>
      </div>
      <footer>
        <button class="button ghost" type="button" :disabled="stoppingBusy" @click="stoppingExecution = null">
          返回</button
        ><button class="button danger" type="button" :disabled="stoppingBusy" @click="forceStopExecution">
          {{ stoppingBusy ? '正在提交…' : '确认强制停止' }}
        </button>
      </footer>
    </section>
  </div>
</template>
