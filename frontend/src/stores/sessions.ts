import { defineStore } from 'pinia'
import * as sessionApi from '@/api/sessions'
import type { Session, SessionCreate } from '@/types/session'

export const useSessionsStore = defineStore('sessions', {
  state: () => ({
    items: [] as Session[],
    current_id: null as string | null,
    loading: false,
    error: null as string | null,
    load_id: 0,
  }),
  actions: {
    async load() {
      const load_id = ++this.load_id
      this.loading = true
      this.error = null
      try {
        const items = await sessionApi.listSessions()
        if (this.load_id === load_id) this.items = items
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
      this.load_id += 1
      this.loading = false
      this.items.unshift(created)
      this.current_id = created.id
      return created
    },
  },
})
