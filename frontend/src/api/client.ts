export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    public readonly code: string | null = null,
  ) {
    super(detail)
    this.name = 'ApiError'
  }
}

function formatDetail(detail: unknown): string | null {
  if (typeof detail === 'string') return detail
  if (typeof detail === 'object' && detail !== null && !Array.isArray(detail)) {
    const message = (detail as { message?: unknown }).message
    return typeof message === 'string' ? message : null
  }
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

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options)
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: unknown } | null
    const detail = payload?.detail
    const code = typeof detail === 'object' && detail !== null && !Array.isArray(detail)
      ? (detail as { code?: unknown }).code
      : null
    throw new ApiError(
      response.status,
      formatDetail(detail) ?? (response.statusText || `请求失败 (${response.status})`),
      typeof code === 'string' ? code : null,
    )
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}
