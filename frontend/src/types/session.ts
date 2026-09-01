export interface Session {
  id: string
  title: string
  workspace_path: string
  status: SessionStatus
  created_at: string
  updated_at: string
}

export type SessionStatus =
  | 'idle'
  | 'running'
  | 'completed'
  | 'failed'
  | 'max_steps'
  | 'cancelled'
  | 'interrupted'

export interface SessionCreate {
  title: string
  workspace_path: string
}
