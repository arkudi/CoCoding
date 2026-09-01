<script setup lang="ts">
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useSessionsStore } from '@/stores/sessions'
import { useRunsStore } from '@/stores/runs'
import Sidebar from './components/Sidebar.vue'
import Timeline from './components/Timeline.vue'
import Workspace from './components/Workspace.vue'

const sessions = useSessionsStore()
const runs = useRunsStore()
const current = computed(() => sessions.items.find(item => item.id === sessions.current_id))
onMounted(() => sessions.load())
onUnmounted(() => runs.disconnect())

watch(
  () => sessions.current_id,
  async (sessionId) => {
    runs.disconnect()
    if (sessionId) await runs.loadHistory(sessionId)
  },
)
</script>

<template>
  <div class="app-shell">
    <Sidebar />
    <Timeline
      :title="current?.title"
      :history="current ? runs.history_by_session[current.id] ?? [] : []"
      :selected="runs.selected"
      :cancelling="runs.cancelling"
      :error="runs.error"
      @select="runs.selectRun"
      @submit="current && runs.submit(current.id, $event)"
      @cancel="runs.requestCancel"
    />
    <Workspace />
  </div>
</template>
