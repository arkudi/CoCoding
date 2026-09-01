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
  },
})
