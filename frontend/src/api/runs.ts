import { request } from './client'
import type { Run, RunCancelResult, RunCreate, RunEvent } from '@/types/run'

const encoded = (value: string) => encodeURIComponent(value)

export const createRun = (sessionId: string, payload: RunCreate) =>
  request<Run>(`/api/sessions/${encoded(sessionId)}/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

export const listRuns = (sessionId: string) =>
  request<Run[]>(`/api/sessions/${encoded(sessionId)}/runs`)

export const getRun = (runId: string) => request<Run>(`/api/runs/${encoded(runId)}`)

export const cancelRun = (runId: string) =>
  request<RunCancelResult>(`/api/runs/${encoded(runId)}/cancel`, { method: 'POST' })

export interface RunEventHandlers {
  onEvent(event: RunEvent): void
  onError(): void
  onClose(): void
}

export interface RunEventConnection {
  close(): void
}

export function connectRunEvents(
  runId: string,
  handlers: RunEventHandlers,
  factory: (url: string) => WebSocket = url => new WebSocket(url),
): RunEventConnection {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const socket = factory(
    `${protocol}//${window.location.host}/api/runs/${encoded(runId)}/events`,
  )
  socket.onmessage = message => {
    try {
      const event = JSON.parse(String(message.data)) as RunEvent
      if (typeof event.type === 'string' && event.run_id === runId) handlers.onEvent(event)
    } catch {
      handlers.onError()
    }
  }
  socket.onerror = () => handlers.onError()
  socket.onclose = () => handlers.onClose()
  return { close: () => socket.close() }
}
