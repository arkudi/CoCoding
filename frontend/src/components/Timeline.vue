<script setup lang="ts">
defineProps<{ title?: string }>()
</script>

<template>
  <main class="timeline" aria-label="执行过程">
    <div class="empty-state">
      <span class="eyebrow">LOCAL CODING AGENT</span>
      <h1>{{ title || '准备开始' }}</h1>
      <p>选择一个工作区，然后创建编程任务。</p>
    </div>
  </main>
</template>
