<script setup lang="ts">
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useSessionsStore } from '@/stores/sessions'
import { useRunsStore } from '@/stores/runs'
import { useWorkspaceStore } from '@/stores/workspace'
import Sidebar from './components/Sidebar.vue'
import Timeline from './components/Timeline.vue'
import Workspace from './components/Workspace.vue'

const sessions = useSessionsStore()
const runs = useRunsStore()
const workspace = useWorkspaceStore()
const current = computed(() => sessions.items.find(item => item.id === sessions.current_id))
onMounted(() => sessions.load())
onUnmounted(() => {
  runs.disconnect()
  workspace.reset()
})

watch(
  () => sessions.current_id,
  async (sessionId) => {
    runs.disconnect()
    workspace.reset()
    if (sessionId) {
      await Promise.all([
        runs.loadHistory(sessionId),
        workspace.loadFiles(sessionId),
      ])
      if (sessions.current_id === sessionId) workspace.startWatching(sessionId)
    }
  },
)
</script>

<template>
  <div class="app-shell">
    <Sidebar />
    <Timeline
      :session-id="current?.id"
      :title="current?.title"
      :history="current ? runs.history_by_session[current.id] ?? [] : []"
      :selected="runs.selected"
      :draft="runs.selected_draft?.text ?? ''"
      :streaming="runs.selected_draft?.active ?? false"
      :cancelling="runs.cancelling"
      :error="runs.error"
      @submit="current && runs.submit(current.id, $event)"
      @cancel="runs.requestCancel"
    />
    <Workspace
      :files="workspace.files"
      :selected-path="workspace.selected_path"
      :preview="workspace.preview"
      :error="workspace.error"
      :loading="workspace.loading"
      :syncing="workspace.syncing"
      :truncated="workspace.truncated"
      :last-synced-at="workspace.last_synced_at"
      :file-changes="runs.selected?.file_changes ?? []"
      @select-file="current && workspace.selectFile(current.id, $event)"
      @refresh="current && workspace.sync(current.id)"
    />
  </div>
</template>
