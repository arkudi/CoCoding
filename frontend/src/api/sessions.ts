import type { Session, SessionCreate } from '@/types/session'
import { request } from './client'

export { ApiError } from './client'

export const listSessions = () => request<Session[]>('/api/sessions')

export const createSession = (payload: SessionCreate) =>
  request<Session>('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
