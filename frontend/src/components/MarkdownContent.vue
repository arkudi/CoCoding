<script setup lang="ts">
import { computed } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

const props = defineProps<{ content: string }>()

const rendered = computed(() => DOMPurify.sanitize(marked.parse(props.content, {
  async: false,
  breaks: true,
  gfm: true,
})))
</script>

<template>
  <div class="markdown-content" v-html="rendered" />
</template>

