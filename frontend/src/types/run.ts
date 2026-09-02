export type RunStatus =
  | 'running'
  | 'completed'
  | 'failed'
  | 'max_steps'
  | 'cancelled'
  | 'interrupted'

export interface RunMessage {
  id: string
  run_id: string
  session_id: string
  role: string
  content: string | null
  tool_calls_json: string | null
  tool_call_id: string | null
  created_at: string
}

export interface ToolCall {
  id: string
  run_id: string
  provider_call_id: string
  name: string
  arguments_json: string
  status: string
  result_json: string | null
  duration_ms: number | null
  started_at: string
  finished_at: string | null
}

export interface FileChange {
  id: string
  run_id: string
  relative_path: string
  operation: string
  before_hash: string | null
  after_hash: string
  unified_diff: string
  created_at: string
}

export interface Run {
  id: string
  session_id: string
  prompt: string
  model: string
  prompt_version: string
  status: RunStatus
  max_steps: number
  step_count: number
  final_response: string | null
  error_text: string | null
  created_at: string
  updated_at: string
  finished_at: string | null
  messages: RunMessage[]
  tool_calls: ToolCall[]
  file_changes: FileChange[]
}

export interface RunCreate {
  prompt: string
  max_steps: number
}

export interface RunCancelResult {
  run_id: string
  status: RunStatus
  requested: boolean
}

export type RunEventType =
  | 'run.snapshot'
  | 'run.started'
  | 'assistant.started'
  | 'assistant.delta'
  | 'assistant.finished'
  | 'message.created'
  | 'tool.started'
  | 'tool.finished'
  | 'files.changed'
  | 'run.finished'
  | 'run.resync_required'

export interface AssistantDeltaData {
  delta: string
}

export interface AssistantDraft {
  text: string
  active: boolean
}

export interface RunEvent {
  type: RunEventType
  run_id: string
  occurred_at: string
  data: unknown
}
