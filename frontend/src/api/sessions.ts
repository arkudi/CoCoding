import type { Session, SessionCreate } from '@/types/session'

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(detail)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options)
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: unknown } | null
    const detail = typeof payload?.detail === 'string' ? payload.detail : response.statusText
    throw new ApiError(response.status, detail || `请求失败 (${response.status})`)
  }
  return response.json() as Promise<T>
}

export const listSessions = () => request<Session[]>('/api/sessions')

export const createSession = (payload: SessionCreate) =>
  request<Session>('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
