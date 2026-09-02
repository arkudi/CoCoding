<script setup lang="ts">
import ToolCallCard from './ToolCallCard.vue'
import type { ToolCall } from '@/types/run'

defineProps<{ calls: ToolCall[] }>()
</script>

<template>
  <details class="tool-chain">
    <summary>
      <span class="tool-icon">⌁</span>
      <strong>工具调用 · {{ calls.length }} 步</strong>
      <span class="tool-chain-hint">点击展开</span>
    </summary>
    <div class="tool-chain-calls">
      <ToolCallCard v-for="call in calls" :key="call.id" :call="call" />
    </div>
  </details>
</template>
