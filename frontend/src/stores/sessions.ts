import { defineStore } from 'pinia'
import * as sessionApi from '@/api/sessions'
import type { Session, SessionCreate } from '@/types/session'
import type { SessionStatus } from '@/types/session'

export const useSessionsStore = defineStore('sessions', {
  state: () => ({
    items: [] as Session[],
    current_id: null as string | null,
    loading: false,
    error: null as string | null,
    load_id: 0,
    deleting_ids: [] as string[],
  }),
  actions: {
    select(session_id: string) {
      this.current_id = session_id
    },
    upsertStatus(session_id: string, status: SessionStatus) {
      const session = this.items.find(item => item.id === session_id)
      if (session) session.status = status
    },
    async load() {
      const load_id = ++this.load_id
      const item_ids_at_start = new Set(this.items.map(item => item.id))
      this.loading = true
      this.error = null
      try {
        const items = await sessionApi.listSessions()
        if (this.load_id === load_id) {
          const added_during_load = this.items.filter(item => !item_ids_at_start.has(item.id))
          const seen = new Set<string>()
          this.items = [...added_during_load, ...items].filter((item) => {
            if (seen.has(item.id)) return false
            seen.add(item.id)
            return true
          })
          if (this.current_id === null && this.items.length > 0) {
            this.current_id = this.items[0].id
          }
        }
      } catch (error) {
        if (this.load_id === load_id) {
          this.error = error instanceof Error ? error.message : '加载失败'
        }
      } finally {
        if (this.load_id === load_id) this.loading = false
      }
    },
    async create(payload: SessionCreate) {
      const created = await sessionApi.createSession(payload)
      this.loading = false
      this.items.unshift(created)
      this.current_id = created.id
      return created
    },
    rename(session_id: string, title: string) {
      const session = this.items.find(item => item.id === session_id)
      if (session) session.title = title
    },
    async createFromPicker() {
      const selection = await sessionApi.selectWorkspace()
      if (!selection.path) return null
      return this.create({ workspace_path: selection.path })
    },
    async remove(session_id: string) {
      if (this.deleting_ids.includes(session_id)) return false
      this.deleting_ids.push(session_id)
      this.error = null
      try {
        await sessionApi.deleteSession(session_id)
        const index = this.items.findIndex(item => item.id === session_id)
        if (index < 0) return true
        this.load_id += 1
        this.loading = false
        const was_current = this.current_id === session_id
        this.items.splice(index, 1)
        if (was_current) {
          this.current_id = this.items[index]?.id ?? this.items[index - 1]?.id ?? null
        }
        return true
      } catch (error) {
        this.error = error instanceof Error ? error.message : '删除任务失败'
        return false
      } finally {
        this.deleting_ids = this.deleting_ids.filter(id => id !== session_id)
      }
    },
  },
})
