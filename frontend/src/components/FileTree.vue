<script setup lang="ts">
defineProps<{ files: string[], selectedPath: string | null }>()
defineEmits<{ select: [path: string] }>()
</script>

<template>
  <nav class="file-tree" aria-label="工作区文件">
    <button
      v-for="path in files" :key="path" type="button"
      :class="{ selected: path === selectedPath }" @click="$emit('select', path)"
    ><span aria-hidden="true">⌑</span>{{ path }}</button>
  </nav>
</template>
