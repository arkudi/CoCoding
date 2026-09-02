<script setup lang="ts">
import ToolCallCard from './ToolCallCard.vue'
import type { AgentExecution, ToolCall } from '@/types/run'

const props = defineProps<{ calls: ToolCall[], executions: AgentExecution[] }>()

function roleFor(call: ToolCall) {
  return props.executions.find(item => item.id === call.agent_execution_id)?.role
}
</script>

<template>
  <details class="tool-chain">
    <summary>
      <span class="tool-icon">⌁</span>
      <strong>工具调用 · {{ calls.length }} 步</strong>
      <span class="tool-chain-hint">点击展开</span>
    </summary>
    <div class="tool-chain-calls">
      <ToolCallCard
        v-for="call in calls" :key="call.id" :call="call" :agent-role="roleFor(call)"
      />
    </div>
  </details>
</template>
