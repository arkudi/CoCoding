<script setup lang="ts">
import type { ToolCall } from '@/types/run'

defineProps<{ call: ToolCall }>()

function formatJson(value: string | null) {
  if (!value) return '暂无结果'
  try {
    return JSON.stringify(JSON.parse(value), null, 2)
  } catch {
    return value
  }
}
</script>

<template>
  <details class="tool-card">
    <summary>
      <span class="tool-icon">⌁</span>
      <strong>{{ call.name }}</strong>
      <span class="status-pill" :data-status="call.status">{{ call.status }}</span>
      <span v-if="call.duration_ms !== null" class="duration">{{ call.duration_ms }} ms</span>
    </summary>
    <div class="tool-evidence">
      <span>参数</span><pre>{{ formatJson(call.arguments_json) }}</pre>
      <span>结果</span><pre>{{ formatJson(call.result_json) }}</pre>
    </div>
  </details>
</template>
