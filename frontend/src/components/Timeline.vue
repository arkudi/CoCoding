<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import RunComposer from './RunComposer.vue'
import ToolChain from './ToolChain.vue'
import AgentTeam from './AgentTeam.vue'
import type { Run, RunCreate } from '@/types/run'

const props = defineProps<{
  sessionId?: string
  title?: string
  history: Run[]
  selected: Run | null
  draft: string
  streaming: boolean
  cancelling: boolean
  error: string | null
}>()
defineEmits<{
  submit: [payload: RunCreate]
  cancel: []
}>()

const statusCopy: Record<string, string> = {
  running: '执行中', completed: '已完成', failed: '失败', max_steps: '达到步数上限',
  cancelled: '已取消', interrupted: '已中断',
}

const chronologicalHistory = computed(() => [...props.history].sort((left, right) => {
  const difference = new Date(left.created_at).getTime() - new Date(right.created_at).getTime()
  return difference || left.id.localeCompare(right.id)
}))
const conversationElement = ref<HTMLElement | null>(null)
const followsLatest = ref(true)
const conversationRevision = computed(() => props.history
  .map(run => `${run.id}:${run.tool_calls.length}:${run.agent_executions.length}:${run.final_response?.length ?? -1}:${run.error_text ?? ''}`)
  .join('|'))

watch([conversationRevision, () => props.draft], async () => {
  await nextTick()
  if (conversationElement.value && followsLatest.value) {
    conversationElement.value.scrollTop = conversationElement.value.scrollHeight
  }
})

watch(() => props.sessionId, async () => {
  followsLatest.value = true
  await nextTick()
  if (conversationElement.value) {
    conversationElement.value.scrollTop = conversationElement.value.scrollHeight
  }
})

function updateFollowState() {
  const element = conversationElement.value
  if (!element) return
  followsLatest.value = element.scrollHeight - element.scrollTop - element.clientHeight <= 80
}

function responseFor(run: Run) {
  if (run.final_response !== null) return run.final_response
  return props.selected?.id === run.id ? props.draft : ''
}

function isStreaming(run: Run) {
  return props.selected?.id === run.id && props.streaming
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
      <section
        ref="conversationElement"
        class="conversation-feed"
        aria-label="对话记录"
        aria-live="polite"
        @scroll="updateFollowState"
      >
        <section v-for="run in chronologicalHistory" :key="run.id" class="conversation-turn">
          <article class="message user-message"><span>你</span><p>{{ run.prompt }}</p></article>
          <AgentTeam :executions="run.agent_executions" />
          <ToolChain v-if="run.tool_calls.length" :calls="run.tool_calls" />
          <article
            v-if="run.final_response !== null || responseFor(run)"
            class="message assistant-message"
            :class="{ streaming: isStreaming(run) }"
          >
            <span>Agent</span>
            <p>{{ responseFor(run) }}</p>
            <small v-if="isStreaming(run)" class="streaming-status" role="status">正在生成</small>
          </article>
          <p v-if="run.error_text" class="run-error" role="alert">{{ run.error_text }}</p>
        </section>
      </section>
      <p v-if="error" class="run-error" role="alert">{{ error }}</p>
      <RunComposer
        :running="selected?.status === 'running'" :cancelling="cancelling"
        @submit="$emit('submit', $event)" @cancel="$emit('cancel')"
      />
    </div>
  </main>
</template>
