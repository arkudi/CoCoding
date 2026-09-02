import { defineStore } from 'pinia'
import * as api from '@/api/runs'
import { useSessionsStore } from './sessions'
import type {
  AgentExecution, AgentTask, AssistantDeltaData, AssistantDraft, FileChange, Run, RunCreate, RunEvent,
  RunMessage, RunStatus, ToolCall,
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
    draft_by_run: {} as Record<string, AssistantDraft>,
    selected_id: null as string | null,
    loading: false,
    error: null as string | null,
    connection: 'disconnected' as ConnectionState,
    cancelling: false,
    generation: 0,
    reconciliation_revision_by_run: {} as Record<string, number>,
    reconnect_attempt: 0,
    reconnect_exhausted: false,
    socket: null as api.RunEventConnection | null,
    reconnect_timer: null as ReturnType<typeof setTimeout> | null,
  }),
  getters: {
    selected(state): Run | null {
      return state.selected_id ? state.details[state.selected_id] ?? null : null
    },
    selected_draft(state): AssistantDraft | null {
      return state.selected_id ? state.draft_by_run[state.selected_id] ?? null : null
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
        else {
          this.disconnect()
          this.selected_id = null
        }
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
      if (event.type === 'assistant.started') {
        this.draft_by_run[event.run_id] = { text: '', active: true }
      } else if (event.type === 'assistant.delta') {
        const data = event.data as Partial<AssistantDeltaData> | null
        if (!data || typeof data.delta !== 'string') {
          this.error = '实时事件格式无效'
          return
        }
        const draft = this.draft_by_run[event.run_id] ?? { text: '', active: true }
        this.draft_by_run[event.run_id] = {
          text: draft.text + data.delta,
          active: true,
        }
      } else if (event.type === 'assistant.finished') {
        const draft = this.draft_by_run[event.run_id]
        if (draft) this.draft_by_run[event.run_id] = { ...draft, active: false }
      } else if (event.type === 'run.snapshot') {
        this.replaceRun(event.data as Run)
      } else if (event.type === 'message.created') {
        run.messages = upsert(run.messages, event.data as RunMessage)
      } else if (event.type === 'agent.started' || event.type === 'agent.finished') {
        run.agent_executions = upsert(run.agent_executions, event.data as AgentExecution)
      } else if (event.type === 'task.created' || event.type === 'task.started' || event.type === 'task.finished') {
        run.agent_tasks = upsert(run.agent_tasks, event.data as AgentTask)
      } else if (event.type === 'tool.started' || event.type === 'tool.finished') {
        run.tool_calls = upsert(run.tool_calls, event.data as ToolCall)
      } else if (event.type === 'files.changed') {
        run.file_changes = event.data as FileChange[]
      } else if (event.type === 'run.finished' || event.type === 'run.resync_required') {
        await this.reconcile(event.run_id)
      }
    },
    async reconcile(runId: string, clearDraftOnFailure = false) {
      const revision = (this.reconciliation_revision_by_run[runId] ?? 0) + 1
      this.reconciliation_revision_by_run[runId] = revision
      try {
        const refreshed = await api.getRun(runId)
        if (this.reconciliation_revision_by_run[runId] !== revision) return
        const isSelected = this.selected_id === runId
        this.replaceRun(refreshed, isSelected)
        if (terminal.has(refreshed.status) && isSelected) {
          this.cancelling = false
          this.disconnect()
        }
      } catch (error) {
        if (this.reconciliation_revision_by_run[runId] === revision) {
          this.error = error instanceof Error && error.message
            ? error.message
            : '同步运行状态失败'
          if (clearDraftOnFailure && this.selected_id === runId) {
            delete this.draft_by_run[runId]
          }
        }
      }
    },
    replaceRun(run: Run, clearDraft = true) {
      if (clearDraft) delete this.draft_by_run[run.id]
      this.details[run.id] = run
      this.history_by_session[run.session_id] = upsert(
        this.history_by_session[run.session_id] ?? [], run,
      )
      useSessionsStore().upsertStatus(run.session_id, run.status)
    },
    scheduleReconnect(runId: string) {
      if (this.details[runId]?.status !== 'running') {
        this.connection = 'disconnected'
        return
      }
      if (this.reconnect_attempt >= 3) {
        this.connection = 'disconnected'
        if (!this.reconnect_exhausted) {
          this.reconnect_exhausted = true
          void this.reconcile(runId, true)
        }
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
      if (this.selected_id) delete this.draft_by_run[this.selected_id]
      this.generation += 1
      if (this.reconnect_timer !== null) clearTimeout(this.reconnect_timer)
      this.reconnect_timer = null
      this.socket?.close()
      this.socket = null
      this.connection = 'disconnected'
      this.reconnect_attempt = 0
      this.reconnect_exhausted = false
    },
  },
})
