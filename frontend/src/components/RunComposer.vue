<script setup lang="ts">
import { ref } from 'vue'
import type { RunCreate } from '@/types/run'

defineProps<{ running: boolean, cancelling: boolean }>()
const emit = defineEmits<{ submit: [payload: RunCreate], cancel: [] }>()
const prompt = ref('')

function submit() {
  const normalized = prompt.value.trim()
  if (!normalized) return
  emit('submit', { prompt: normalized })
  prompt.value = ''
}
</script>

<template>
  <form class="run-composer" @submit.prevent="submit">
    <label for="run-prompt">任务描述</label>
    <textarea
      id="run-prompt"
      v-model="prompt"
      rows="4"
      placeholder="描述你希望 Agent 完成的工作…"
      :disabled="running"
    />
    <div class="composer-actions">
      <button v-if="!running" class="primary compact" type="submit" :disabled="!prompt.trim()">运行任务</button>
      <button v-else class="danger" type="button" :disabled="cancelling" @click="emit('cancel')">
        {{ cancelling ? '正在取消' : '取消任务' }}
      </button>
    </div>
  </form>
</template>
