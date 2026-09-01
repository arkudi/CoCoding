import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, test, vi } from 'vitest'
import { useRunsStore } from './runs'
import type { Run, RunEvent } from '@/types/run'

const api = vi.hoisted(() => ({
  createRun: vi.fn(), listRuns: vi.fn(), getRun: vi.fn(), cancelRun: vi.fn(),
  connectRunEvents: vi.fn(),
}))
vi.mock('@/api/runs', () => api)

const running: Run = {
  id: 'run-1', session_id: 'session-1', prompt: 'inspect', model: 'fake',
  prompt_version: 'v1', status: 'running', max_steps: 20, step_count: 0,
  final_response: null, error_text: null, created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z', finished_at: null,
  messages: [], tool_calls: [], file_changes: [],
}

let handlers: { onEvent(event: RunEvent): void, onError(): void, onClose(): void }

function event(type: RunEvent['type'], data: unknown, runId = 'run-1'): RunEvent {
  return { type, run_id: runId, occurred_at: running.updated_at, data }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  api.connectRunEvents.mockImplementation((_id, nextHandlers) => {
    handlers = nextHandlers
    return { close: vi.fn() }
  })
})

test('submits, selects, and subscribes to a running run', async () => {
  api.createRun.mockResolvedValue(running)
  const store = useRunsStore()

  await store.submit('session-1', { prompt: 'inspect', max_steps: 20 })

  expect(store.selected_id).toBe('run-1')
  expect(api.connectRunEvents).toHaveBeenCalled()
})

test('reconciles durable state after a terminal event', async () => {
  api.createRun.mockResolvedValue(running)
  const completed = { ...running, status: 'completed' as const, final_response: 'Done.' }
  api.getRun.mockResolvedValue(completed)
  const store = useRunsStore()
  await store.submit('session-1', { prompt: 'inspect', max_steps: 20 })

  handlers.onEvent({
    type: 'run.finished', run_id: 'run-1', occurred_at: running.updated_at,
    data: completed,
  })
  await vi.waitFor(() => expect(store.selected?.status).toBe('completed'))

  expect(api.getRun).toHaveBeenCalledWith('run-1')
  expect(store.selected?.final_response).toBe('Done.')
})

test('accumulates assistant deltas until terminal reconciliation', async () => {
  api.createRun.mockResolvedValue(running)
  api.getRun.mockResolvedValue({
    ...running, status: 'completed', final_response: 'Hello world',
  })
  const store = useRunsStore()
  await store.submit('session-1', { prompt: 'inspect', max_steps: 20 })

  handlers.onEvent(event('assistant.started', {}))
  handlers.onEvent(event('assistant.delta', { delta: 'Hello' }))
  handlers.onEvent(event('assistant.delta', { delta: ' world' }))
  handlers.onEvent(event('assistant.finished', {}))
  expect(store.selected_draft).toEqual({ text: 'Hello world', active: false })

  handlers.onEvent(event('run.finished', {}))
  await vi.waitFor(() => expect(store.selected_draft).toBeNull())
  expect(store.selected?.final_response).toBe('Hello world')
})

test('keeps the live draft when a durable message arrives', async () => {
  api.createRun.mockResolvedValue(running)
  const store = useRunsStore()
  await store.submit('session-1', { prompt: 'inspect', max_steps: 20 })

  handlers.onEvent(event('assistant.started', {}))
  handlers.onEvent(event('assistant.delta', { delta: 'Hello' }))
  handlers.onEvent(event('message.created', {
    id: 'message-1', run_id: 'run-1', session_id: 'session-1', role: 'assistant',
    content: 'Hello', tool_calls_json: null, tool_call_id: null, created_at: running.updated_at,
  }))

  expect(store.selected_draft).toEqual({ text: 'Hello', active: true })
})

test('replaces a stale draft when a new assistant turn starts', async () => {
  api.createRun.mockResolvedValue(running)
  const store = useRunsStore()
  await store.submit('session-1', { prompt: 'inspect', max_steps: 20 })

  handlers.onEvent(event('assistant.started', {}))
  handlers.onEvent(event('assistant.delta', { delta: 'stale' }))
  handlers.onEvent(event('assistant.started', {}))

  expect(store.selected_draft).toEqual({ text: '', active: true })
})

test('clears a stale draft when an authoritative snapshot arrives', async () => {
  api.createRun.mockResolvedValue(running)
  const store = useRunsStore()
  await store.submit('session-1', { prompt: 'inspect', max_steps: 20 })

  handlers.onEvent(event('assistant.started', {}))
  handlers.onEvent(event('assistant.delta', { delta: 'stale' }))
  handlers.onEvent(event('run.snapshot', { ...running, final_response: 'durable' }))

  expect(store.selected_draft).toBeNull()
  expect(store.selected?.final_response).toBe('durable')
})

test.each(['failed', 'cancelled'] as const)(
  'clears a stale draft after %s terminal reconciliation',
  async status => {
    api.createRun.mockResolvedValue(running)
    api.getRun.mockResolvedValue({ ...running, status })
    const store = useRunsStore()
    await store.submit('session-1', { prompt: 'inspect', max_steps: 20 })

    handlers.onEvent(event('assistant.started', {}))
    handlers.onEvent(event('assistant.delta', { delta: 'stale' }))
    handlers.onEvent(event('run.finished', {}))

    await vi.waitFor(() => expect(store.selected_draft).toBeNull())
  },
)

test('clears the stale draft when the selected run changes', async () => {
  api.createRun.mockResolvedValue(running)
  const store = useRunsStore()
  await store.submit('session-1', { prompt: 'inspect', max_steps: 20 })
  handlers.onEvent(event('assistant.started', {}))
  handlers.onEvent(event('assistant.delta', { delta: 'stale' }))
  store.details['run-2'] = { ...running, id: 'run-2', status: 'completed' }

  store.selectRun('run-2')

  expect(store.selected_draft).toBeNull()
  expect(store.draft_by_run['run-1']).toBeUndefined()
})

test('clears the stale draft on explicit disconnect', async () => {
  api.createRun.mockResolvedValue(running)
  const store = useRunsStore()
  await store.submit('session-1', { prompt: 'inspect', max_steps: 20 })
  handlers.onEvent(event('assistant.started', {}))
  handlers.onEvent(event('assistant.delta', { delta: 'stale' }))

  store.disconnect()

  expect(store.selected_draft).toBeNull()
  expect(store.draft_by_run['run-1']).toBeUndefined()
})

test('preserves the draft while a closed socket schedules reconnect', async () => {
  api.createRun.mockResolvedValue(running)
  const store = useRunsStore()
  await store.submit('session-1', { prompt: 'inspect', max_steps: 20 })
  handlers.onEvent(event('assistant.started', {}))
  handlers.onEvent(event('assistant.delta', { delta: 'Hello' }))

  handlers.onClose()

  expect(store.selected_draft).toEqual({ text: 'Hello', active: true })
})

test('rejects malformed assistant deltas without changing the draft', async () => {
  api.createRun.mockResolvedValue(running)
  const store = useRunsStore()
  await store.submit('session-1', { prompt: 'inspect', max_steps: 20 })
  handlers.onEvent(event('assistant.started', {}))
  handlers.onEvent(event('assistant.delta', { delta: 'Hello' }))

  handlers.onEvent(event('assistant.delta', { delta: 42 }))

  expect(store.selected_draft).toEqual({ text: 'Hello', active: true })
  expect(store.error).toBe('实时事件格式无效')
})

test('requests cancellation once while pending', async () => {
  api.createRun.mockResolvedValue(running)
  api.cancelRun.mockResolvedValue({ run_id: 'run-1', status: 'running', requested: true })
  const store = useRunsStore()
  await store.submit('session-1', { prompt: 'inspect', max_steps: 20 })

  await store.requestCancel()

  expect(api.cancelRun).toHaveBeenCalledTimes(1)
  expect(store.cancelling).toBe(true)
})
