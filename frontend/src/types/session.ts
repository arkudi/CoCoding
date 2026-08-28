export interface Session {
  id: string
  title: string
  workspace_path: string
  status: 'idle' | 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted'
  created_at: string
  updated_at: string
}

export interface SessionCreate {
  title: string
  workspace_path: string
}
