<script setup>
import { useWorkspaceContext } from '../../workspace/context'

const { confirmDelete, deletingTask } = useWorkspaceContext()
</script>

<template>
  <div class="modal-layer" @mousedown.self="deletingTask = null">
    <section class="modal confirm-modal" role="alertdialog" aria-modal="true" aria-label="删除任务">
      <header>
        <div>
          <span class="eyebrow danger-text">DELETE TASK</span>
          <h2>删除“{{ deletingTask.name }}”</h2>
        </div>
        <button class="modal-close" type="button" aria-label="关闭" @click="deletingTask = null">×</button>
      </header>
      <div class="modal-body confirm-copy">
        <p><strong>删除后无法恢复。</strong></p>
        <ul>
          <li>删除任务和全部运行记录</li>
          <li>删除 Python 脚本、模板、依赖和独立 .venv</li>
        </ul>
      </div>
      <footer>
        <button class="button ghost" type="button" @click="deletingTask = null">取消</button
        ><button class="button danger" type="button" @click="confirmDelete">确认彻底删除</button>
      </footer>
    </section>
  </div>
</template>
