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

test('requests cancellation once while pending', async () => {
  api.createRun.mockResolvedValue(running)
  api.cancelRun.mockResolvedValue({ run_id: 'run-1', status: 'running', requested: true })
  const store = useRunsStore()
  await store.submit('session-1', { prompt: 'inspect', max_steps: 20 })

  await store.requestCancel()

  expect(api.cancelRun).toHaveBeenCalledTimes(1)
  expect(store.cancelling).toBe(true)
})
