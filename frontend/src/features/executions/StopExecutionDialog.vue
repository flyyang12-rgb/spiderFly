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
        <p>将终止本次 Python 进程及其子进程，未保存的内容可能丢失，已经完成的业务操作不会撤销。</p>
        <p>保留运行记录和日志；进程退出并清理完成后，队列继续执行下一项。</p>
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
