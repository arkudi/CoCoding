<script setup lang="ts">
import { computed, ref } from 'vue'
import FileTree from './FileTree.vue'
import type { WorkspaceFile } from '@/api/workspace'
import type { FileChange } from '@/types/run'

const props = defineProps<{
  files: string[]
  selectedPath: string | null
  preview: WorkspaceFile | null
  error: string | null
  fileChanges: FileChange[]
  loading: boolean
  syncing: boolean
  truncated: boolean
  lastSyncedAt: number | null
}>()
defineEmits<{ selectFile: [path: string], refresh: [] }>()
const tab = ref<'files' | 'diff'>('files')
const selectedDiff = ref<string | null>(null)
const diff = computed(() => props.fileChanges.find(item => item.relative_path === selectedDiff.value) ?? null)
</script>

<template>
  <aside class="workspace" aria-label="工作区">
    <div class="tabs" role="tablist" aria-label="工作区视图">
      <button class="tab" :class="{ active: tab === 'files' }" role="tab" :aria-selected="tab === 'files'" @click="tab = 'files'">文件</button>
      <button class="tab" :class="{ active: tab === 'diff' }" role="tab" :aria-selected="tab === 'diff'" @click="tab = 'diff'">Diff</button>
    </div>
    <section v-if="tab === 'files'" class="workspace-pane" role="tabpanel">
      <div class="file-sync-bar">
        <span><strong>{{ files.length }}</strong> files</span>
        <span v-if="truncated" class="tree-warning">仅显示部分文件</span>
        <span
          v-else class="live-indicator" :class="{ syncing }"
          :title="lastSyncedAt ? `上次同步 ${new Date(lastSyncedAt).toLocaleTimeString()}` : '等待首次同步'"
        >
          <i aria-hidden="true" />{{ syncing ? '同步中' : '实时同步' }}
        </span>
        <button type="button" :disabled="loading || syncing" @click="$emit('refresh')">刷新</button>
      </div>
      <FileTree :files="files" :selected-path="selectedPath" @select="$emit('selectFile', $event)" />
      <p v-if="files.length === 0" class="empty-copy">工作区中没有可预览文件</p>
      <p v-if="error" class="run-error" role="alert">{{ error }}</p>
      <div v-if="preview" class="code-preview"><strong>{{ preview.path }}</strong><pre>{{ preview.content }}</pre></div>
      <p v-else-if="files.length" class="empty-copy">选择文件以预览内容</p>
    </section>
    <section v-else class="workspace-pane" role="tabpanel">
      <div class="diff-list">
        <button v-for="change in fileChanges" :key="change.id" type="button" :aria-label="change.relative_path" @click="selectedDiff = change.relative_path">
          <span>{{ change.operation }}</span>{{ change.relative_path }}
        </button>
      </div>
      <p v-if="fileChanges.length === 0" class="empty-copy">当前任务没有文件修改</p>
      <pre v-if="diff" class="diff-preview">{{ diff.unified_diff }}</pre>
      <p v-else-if="fileChanges.length" class="empty-copy">选择文件以查看 Diff</p>
    </section>
  </aside>
</template>
