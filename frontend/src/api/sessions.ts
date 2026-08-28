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

function formatDetail(detail: unknown): string | null {
  if (typeof detail === 'string') return detail
  if (!Array.isArray(detail)) return null

  const messages = detail.flatMap((item) => {
    if (typeof item !== 'object' || item === null) return []
    const { loc, msg } = item as { loc?: unknown, msg?: unknown }
    if (typeof msg !== 'string') return []
    const field = Array.isArray(loc) ? loc.at(-1) : null
    return [typeof field === 'string' ? `${field}: ${msg}` : msg]
  })
  return messages.length > 0 ? messages.join('; ') : null
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options)
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: unknown } | null
    const detail = formatDetail(payload?.detail) ?? response.statusText
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
