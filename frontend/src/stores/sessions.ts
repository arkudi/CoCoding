import { defineStore } from 'pinia'
import * as sessionApi from '@/api/sessions'
import type { Session, SessionCreate } from '@/types/session'

export const useSessionsStore = defineStore('sessions', {
  state: () => ({
    items: [] as Session[],
    current_id: null as string | null,
    loading: false,
    error: null as string | null,
  }),
  actions: {
    async load() {
      this.loading = true
      this.error = null
      try {
        this.items = await sessionApi.listSessions()
      } catch (error) {
        this.error = error instanceof Error ? error.message : '加载失败'
      } finally {
        this.loading = false
      }
    },
    async create(payload: SessionCreate) {
      const created = await sessionApi.createSession(payload)
      this.items.unshift(created)
      this.current_id = created.id
      return created
    },
  },
})
