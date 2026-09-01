import { defineStore } from 'pinia'
import { ApiError } from '@/api/client'
import * as api from '@/api/workspace'
import type { WorkspaceFile } from '@/api/workspace'

function previewError(error: unknown): string {
  if (error instanceof ApiError && error.code === 'INVALID_UTF8') return '无法预览二进制文件'
  if (error instanceof ApiError && error.code === 'FILE_TOO_LARGE') return '文件过大，无法预览'
  return error instanceof Error ? error.message : '文件加载失败'
}

export const useWorkspaceStore = defineStore('workspace', {
  state: () => ({
    files: [] as string[],
    truncated: false,
    selected_path: null as string | null,
    preview: null as WorkspaceFile | null,
    loading: false,
    error: null as string | null,
    generation: 0,
  }),
  actions: {
    async loadFiles(sessionId: string) {
      const generation = ++this.generation
      this.loading = true
      this.error = null
      try {
        const result = await api.listWorkspaceFiles(sessionId)
        if (generation !== this.generation) return
        this.files = result.files
        this.truncated = result.truncated
      } catch (error) {
        if (generation === this.generation) this.error = previewError(error)
      } finally {
        if (generation === this.generation) this.loading = false
      }
    },
    async selectFile(sessionId: string, path: string) {
      const generation = ++this.generation
      this.selected_path = path
      this.preview = null
      this.loading = true
      this.error = null
      try {
        const result = await api.readWorkspaceFile(sessionId, path)
        if (generation === this.generation) this.preview = result
      } catch (error) {
        if (generation === this.generation) this.error = previewError(error)
      } finally {
        if (generation === this.generation) this.loading = false
      }
    },
    reset() {
      this.generation += 1
      this.files = []
      this.truncated = false
      this.selected_path = null
      this.preview = null
      this.loading = false
      this.error = null
    },
  },
})
