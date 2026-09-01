<script setup lang="ts">
import RunComposer from './RunComposer.vue'
import ToolCallCard from './ToolCallCard.vue'
import type { Run, RunCreate } from '@/types/run'

defineProps<{
  title?: string
  history: Run[]
  selected: Run | null
  draft: string
  streaming: boolean
  cancelling: boolean
  error: string | null
}>()
defineEmits<{
  select: [runId: string]
  submit: [payload: RunCreate]
  cancel: []
}>()

const statusCopy: Record<string, string> = {
  running: '执行中', completed: '已完成', failed: '失败', max_steps: '达到步数上限',
  cancelled: '已取消', interrupted: '已中断',
}
</script>

<template>
  <main class="timeline" aria-label="执行过程">
    <div v-if="!title" class="empty-state">
      <span class="eyebrow">LOCAL CODING AGENT</span>
      <h1>准备开始</h1>
      <p>创建或选择一个本地工作区，然后提交编程任务。</p>
    </div>
    <div v-else class="timeline-shell">
      <header class="timeline-header">
        <div><span class="eyebrow">LOCAL AGENT SESSION</span><h1>{{ title }}</h1></div>
        <span v-if="selected" class="run-status" :data-status="selected.status">{{ statusCopy[selected.status] }}</span>
      </header>
      <nav v-if="history.length" class="run-history" aria-label="运行历史">
        <button
          v-for="run in history" :key="run.id" type="button"
          :class="{ selected: selected?.id === run.id }" @click="$emit('select', run.id)"
        >{{ new Date(run.created_at).toLocaleString() }} · {{ statusCopy[run.status] }}</button>
      </nav>
      <section v-if="selected" class="run-evidence" aria-live="polite">
        <article class="message user-message"><span>任务</span><p>{{ selected.prompt }}</p></article>
        <ToolCallCard v-for="call in selected.tool_calls" :key="call.id" :call="call" />
        <article
          v-if="selected.final_response || draft"
          class="message assistant-message"
          :class="{ streaming }"
        >
          <span>Agent</span>
          <p>{{ selected.final_response || draft }}</p>
          <small v-if="streaming" class="streaming-status" role="status">正在生成</small>
        </article>
        <p v-if="selected.error_text" class="run-error" role="alert">{{ selected.error_text }}</p>
      </section>
      <p v-if="error" class="run-error" role="alert">{{ error }}</p>
      <RunComposer
        :running="selected?.status === 'running'" :cancelling="cancelling"
        @submit="$emit('submit', $event)" @cancel="$emit('cancel')"
      />
    </div>
  </main>
</template>
