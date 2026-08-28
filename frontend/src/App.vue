<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useSessionsStore } from '@/stores/sessions'
import Sidebar from './components/Sidebar.vue'
import Timeline from './components/Timeline.vue'
import Workspace from './components/Workspace.vue'

const sessions = useSessionsStore()
const current = computed(() => sessions.items.find(item => item.id === sessions.current_id))
onMounted(() => sessions.load())
</script>

<template>
  <div class="app-shell">
    <Sidebar />
    <Timeline :title="current?.title" />
    <Workspace />
  </div>
</template>
