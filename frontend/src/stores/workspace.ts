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
    syncing: false,
    error: null as string | null,
    last_synced_at: null as number | null,
    list_generation: 0,
    preview_generation: 0,
    watcher: null as ReturnType<typeof setInterval> | null,
    watched_session_id: null as string | null,
    sync_in_flight: false,
  }),
  actions: {
    async loadFiles(sessionId: string, background = false) {
      const generation = ++this.list_generation
      if (background) this.syncing = true
      else this.loading = true
      if (!background) this.error = null
      try {
        const result = await api.listWorkspaceFiles(sessionId)
        if (generation !== this.list_generation) return
        this.files = result.files
        this.truncated = result.truncated
        this.last_synced_at = Date.now()
        if (this.selected_path && !this.files.includes(this.selected_path)) {
          this.selected_path = null
          this.preview = null
        }
      } catch (error) {
        if (generation === this.list_generation) this.error = previewError(error)
      } finally {
        if (generation === this.list_generation) {
          this.loading = false
          this.syncing = false
        }
      }
    },
    async selectFile(sessionId: string, path: string, background = false) {
      const generation = ++this.preview_generation
      this.selected_path = path
      if (!background) {
        this.preview = null
        this.loading = true
        this.error = null
      }
      try {
        const result = await api.readWorkspaceFile(sessionId, path)
        if (generation === this.preview_generation && this.selected_path === path) {
          this.preview = result
        }
      } catch (error) {
        if (generation === this.preview_generation) this.error = previewError(error)
      } finally {
        if (generation === this.preview_generation && !background) this.loading = false
      }
    },
    startWatching(sessionId: string) {
      this.stopWatching()
      this.watched_session_id = sessionId
      this.watcher = setInterval(() => { void this.sync(sessionId) }, 1_500)
    },
    stopWatching() {
      if (this.watcher !== null) clearInterval(this.watcher)
      this.watcher = null
      this.watched_session_id = null
      this.sync_in_flight = false
    },
    async sync(sessionId: string) {
      if (this.sync_in_flight || this.watched_session_id !== sessionId) return
      this.sync_in_flight = true
      try {
        await this.loadFiles(sessionId, true)
        if (this.watched_session_id !== sessionId) return
        const selected = this.selected_path
        if (selected && this.files.includes(selected)) {
          await this.selectFile(sessionId, selected, true)
        }
      } finally {
        this.sync_in_flight = false
      }
    },
    reset() {
      this.stopWatching()
      this.list_generation += 1
      this.preview_generation += 1
      this.files = []
      this.truncated = false
      this.selected_path = null
      this.preview = null
      this.loading = false
      this.syncing = false
      this.error = null
      this.last_synced_at = null
    },
  },
})
