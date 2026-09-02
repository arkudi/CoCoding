import { expect, test, vi } from 'vitest'
import { cancelRun, createRun, listRuns } from './runs'

const run = {
  id: 'run-1', session_id: 'session-1', session_title: 'Demo', prompt: 'inspect', model: 'fake',
  prompt_version: 'v1', status: 'running', max_steps: 20, step_count: 0,
  final_response: null, error_text: null, created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z', finished_at: null,
  messages: [], tool_calls: [], file_changes: [], agent_executions: [], agent_tasks: [],
}

test('creates and lists runs with encoded identifiers', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(run), { status: 202 }))
    .mockResolvedValueOnce(new Response(JSON.stringify([run]), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)

  await createRun('session / one', { prompt: 'inspect' })
  await listRuns('session / one')

  expect(fetchMock.mock.calls[0][0]).toBe('/api/sessions/session%20%2F%20one/runs')
  expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ prompt: 'inspect' })
  expect(fetchMock.mock.calls[1][0]).toBe('/api/sessions/session%20%2F%20one/runs')
})

test('requests cooperative cancellation', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    run_id: 'run-1', status: 'running', requested: true,
  }), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)

  await cancelRun('run-1')

  expect(fetchMock).toHaveBeenCalledWith('/api/runs/run-1/cancel', { method: 'POST' })
})
