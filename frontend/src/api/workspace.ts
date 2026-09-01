import { request } from './client'

export interface WorkspaceFiles {
  files: string[]
  truncated: boolean
}

export interface WorkspaceFile {
  path: string
  content: string
  size: number
}

export const listWorkspaceFiles = (sessionId: string) =>
  request<WorkspaceFiles>(`/api/sessions/${encodeURIComponent(sessionId)}/files`)

export const readWorkspaceFile = (sessionId: string, path: string) => {
  const query = new URLSearchParams({ path })
  return request<WorkspaceFile>(
    `/api/sessions/${encodeURIComponent(sessionId)}/files/content?${query.toString()}`,
  )
}
