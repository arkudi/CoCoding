<script setup lang="ts">
import { ref } from 'vue'
import { useSessionsStore } from '@/stores/sessions'

const sessions = useSessionsStore()
const creating = ref(false)
const submitting = ref(false)
const title = ref('')
const workspace_path = ref('')

async function submit() {
  submitting.value = true
  sessions.error = null
  try {
    await sessions.create({ title: title.value, workspace_path: workspace_path.value })
    title.value = ''
    workspace_path.value = ''
    creating.value = false
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
    <button v-if="!creating" class="primary" type="button" @click="creating = true">新建任务</button>
    <form v-else class="session-form" @submit.prevent="submit">
      <label>任务名称<input v-model="title" required /></label>
      <label>工作区路径<input v-model="workspace_path" required /></label>
      <div class="form-actions">
        <button type="button" @click="creating = false">取消</button>
        <button class="primary" type="submit" :disabled="submitting">创建</button>
      </div>
    </form>
    <p v-if="sessions.error" role="alert">{{ sessions.error }}</p>
    <nav class="session-list" aria-label="任务历史">
      <button
        v-for="session in sessions.items"
        :key="session.id"
        type="button"
        :class="{ selected: session.id === sessions.current_id }"
        @click="sessions.current_id = session.id"
      >
        {{ session.title }}
      </button>
      <p v-if="!sessions.loading && sessions.items.length === 0" class="empty-copy">还没有任务记录</p>
    </nav>
  </aside>
</template>
