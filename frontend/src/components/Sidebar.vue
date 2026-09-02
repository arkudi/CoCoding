<script setup lang="ts">
import { ref } from 'vue'
import { useSessionsStore } from '@/stores/sessions'

const sessions = useSessionsStore()
const submitting = ref(false)
const confirmingId = ref<string | null>(null)

async function createFromPicker() {
  submitting.value = true
  sessions.error = null
  try {
    await sessions.createFromPicker()
  } catch (error) {
    sessions.error = error instanceof Error ? error.message : '创建失败'
  } finally {
    submitting.value = false
  }
}

async function confirmDelete(sessionId: string) {
  if (await sessions.remove(sessionId)) confirmingId.value = null
}
</script>

<template>
  <aside class="sidebar" aria-label="任务">
    <div class="brand"><span class="brand-mark">C</span><strong>CoCoding</strong></div>
    <button class="primary" type="button" :disabled="submitting" @click="createFromPicker">
      {{ submitting ? '选择中…' : '新建任务' }}
    </button>
    <p v-if="sessions.error" role="alert">{{ sessions.error }}</p>
    <nav class="session-list" aria-label="任务历史">
      <div
        v-for="session in sessions.items"
        :key="session.id"
        class="session-entry"
        :class="{ selected: session.id === sessions.current_id, confirming: confirmingId === session.id }"
      >
        <template v-if="confirmingId === session.id">
          <span class="delete-copy">只删除任务记录？</span>
          <button
            class="delete-confirm" type="button"
            :disabled="sessions.deleting_ids.includes(session.id)"
            @click="confirmDelete(session.id)"
          >{{ sessions.deleting_ids.includes(session.id) ? '删除中…' : '确认' }}</button>
          <button class="delete-cancel" type="button" @click="confirmingId = null">取消</button>
        </template>
        <template v-else>
          <button
            type="button"
            class="session-select"
            :aria-label="session.title"
            @click="sessions.select(session.id)"
          >
            <span>{{ session.title }}</span><small>{{ session.status }}</small>
          </button>
          <button
            class="session-delete" type="button"
            :aria-label="`删除任务 ${session.title}`"
            :disabled="session.status === 'running'"
            :title="session.status === 'running' ? '请先取消正在执行的任务' : '删除任务记录，不删除项目文件'"
            @click="confirmingId = session.id"
          >×</button>
        </template>
      </div>
      <p v-if="!sessions.loading && sessions.items.length === 0" class="empty-copy">还没有任务记录</p>
    </nav>
  </aside>
</template>
