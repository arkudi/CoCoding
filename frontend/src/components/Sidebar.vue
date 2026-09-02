<script setup lang="ts">
import { ref } from 'vue'
import { useSessionsStore } from '@/stores/sessions'

const sessions = useSessionsStore()
const submitting = ref(false)

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
</script>

<template>
  <aside class="sidebar" aria-label="任务">
    <div class="brand"><span class="brand-mark">C</span><strong>CoCoding</strong></div>
    <button class="primary" type="button" :disabled="submitting" @click="createFromPicker">
      {{ submitting ? '选择中…' : '新建任务' }}
    </button>
    <p v-if="sessions.error" role="alert">{{ sessions.error }}</p>
    <nav class="session-list" aria-label="任务历史">
      <button
        v-for="session in sessions.items"
        :key="session.id"
        type="button"
        :aria-label="session.title"
        :class="{ selected: session.id === sessions.current_id }"
        @click="sessions.select(session.id)"
      >
        <span>{{ session.title }}</span><small>{{ session.status }}</small>
      </button>
      <p v-if="!sessions.loading && sessions.items.length === 0" class="empty-copy">还没有任务记录</p>
    </nav>
  </aside>
</template>
