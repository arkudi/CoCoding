import type { Session, SessionCreate } from '@/types/session'
import { request } from './client'

export { ApiError } from './client'

export const listSessions = () => request<Session[]>('/api/sessions')

export const selectWorkspace = (initialPath?: string | null) =>
  request<{ path: string | null }>('/api/sessions/select-workspace', {
    method: 'POST',
    ...(initialPath ? {
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ initial_path: initialPath }),
    } : {}),
  })

export const createSession = (payload: SessionCreate) =>
  request<Session>('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

export const deleteSession = (sessionId: string) =>
  request<void>(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
