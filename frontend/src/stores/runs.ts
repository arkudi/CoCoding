import { defineStore } from 'pinia'
import * as api from '@/api/runs'
import { useSessionsStore } from './sessions'
import type {
  FileChange, Run, RunCreate, RunEvent, RunMessage, RunStatus, ToolCall,
} from '@/types/run'

type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'retrying'
const terminal = new Set<RunStatus>([
  'completed', 'failed', 'max_steps', 'cancelled', 'interrupted',
])

function upsert<T extends { id: string }>(items: T[], value: T): T[] {
  const index = items.findIndex(item => item.id === value.id)
  if (index < 0) return [...items, value]
  const copy = [...items]
  copy[index] = value
  return copy
}

export const useRunsStore = defineStore('runs', {
  state: () => ({
    history_by_session: {} as Record<string, Run[]>,
    details: {} as Record<string, Run>,
    selected_id: null as string | null,
    loading: false,
    error: null as string | null,
    connection: 'disconnected' as ConnectionState,
    cancelling: false,
    generation: 0,
    reconnect_attempt: 0,
    socket: null as api.RunEventConnection | null,
    reconnect_timer: null as ReturnType<typeof setTimeout> | null,
  }),
  getters: {
    selected(state): Run | null {
      return state.selected_id ? state.details[state.selected_id] ?? null : null
    },
  },
  actions: {
    async loadHistory(sessionId: string) {
      this.loading = true
      this.error = null
      try {
        const items = await api.listRuns(sessionId)
        this.history_by_session[sessionId] = items
        for (const item of items) this.details[item.id] = item
        if (items.length > 0) this.selectRun(items[0].id)
        else this.selected_id = null
      } catch (error) {
        this.error = error instanceof Error ? error.message : '加载运行记录失败'
      } finally {
        this.loading = false
      }
    },
    async submit(sessionId: string, payload: RunCreate) {
      this.error = null
      const created = await api.createRun(sessionId, payload)
      this.details[created.id] = created
      this.history_by_session[sessionId] = upsert(
        this.history_by_session[sessionId] ?? [], created,
      )
      useSessionsStore().upsertStatus(sessionId, 'running')
      this.selectRun(created.id)
      return created
    },
    selectRun(runId: string) {
      this.disconnect()
      this.selected_id = runId
      this.cancelling = false
      if (this.details[runId]?.status === 'running') this.openConnection(runId)
    },
    openConnection(runId: string) {
      const generation = ++this.generation
      this.connection = 'connecting'
      this.socket = api.connectRunEvents(runId, {
        onEvent: event => {
          if (generation !== this.generation) return
          this.connection = 'connected'
          void this.applyEvent(event)
        },
        onError: () => {
          if (generation === this.generation) this.error = '实时连接暂时不可用'
        },
        onClose: () => {
          if (generation === this.generation) this.scheduleReconnect(runId)
        },
      })
    },
    async applyEvent(event: RunEvent) {
      if (event.run_id !== this.selected_id) return
      const run = this.details[event.run_id]
      if (!run) return
      if (event.type === 'run.snapshot') {
        this.replaceRun(event.data as Run)
      } else if (event.type === 'message.created') {
        run.messages = upsert(run.messages, event.data as RunMessage)
      } else if (event.type === 'tool.started' || event.type === 'tool.finished') {
        run.tool_calls = upsert(run.tool_calls, event.data as ToolCall)
      } else if (event.type === 'files.changed') {
        run.file_changes = event.data as FileChange[]
      } else if (event.type === 'run.finished' || event.type === 'run.resync_required') {
        await this.reconcile(event.run_id)
      }
    },
    async reconcile(runId: string) {
      try {
        const refreshed = await api.getRun(runId)
        this.replaceRun(refreshed)
        if (terminal.has(refreshed.status)) {
          this.cancelling = false
          this.disconnect()
        }
      } catch (error) {
        this.error = error instanceof Error ? error.message : '同步运行状态失败'
      }
    },
    replaceRun(run: Run) {
      this.details[run.id] = run
      this.history_by_session[run.session_id] = upsert(
        this.history_by_session[run.session_id] ?? [], run,
      )
      useSessionsStore().upsertStatus(run.session_id, run.status)
    },
    scheduleReconnect(runId: string) {
      if (this.details[runId]?.status !== 'running' || this.reconnect_attempt >= 3) {
        this.connection = 'disconnected'
        return
      }
      const delay = [250, 500, 1000][this.reconnect_attempt++]
      this.connection = 'retrying'
      const generation = this.generation
      this.reconnect_timer = setTimeout(() => {
        if (generation === this.generation && this.selected_id === runId) {
          this.openConnection(runId)
        }
      }, delay)
    },
    async requestCancel() {
      const run = this.selected
      if (!run || run.status !== 'running' || this.cancelling) return
      this.cancelling = true
      try {
        await api.cancelRun(run.id)
      } catch (error) {
        this.cancelling = false
        this.error = error instanceof Error ? error.message : '取消失败'
      }
    },
    disconnect() {
      this.generation += 1
      if (this.reconnect_timer !== null) clearTimeout(this.reconnect_timer)
      this.reconnect_timer = null
      this.socket?.close()
      this.socket = null
      this.connection = 'disconnected'
      this.reconnect_attempt = 0
    },
  },
})
