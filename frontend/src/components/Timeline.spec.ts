import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { nextTick } from 'vue'
import { expect, test } from 'vitest'
import Timeline from './Timeline.vue'
import type { Run } from '@/types/run'

const running: Run = {
  id: 'run-running', session_id: 'session-1', prompt: 'Inspect the project', model: 'fake',
  prompt_version: 'v1', status: 'running', max_steps: 20, step_count: 1,
  final_response: null, error_text: null,
  created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:01Z',
  finished_at: null, messages: [], tool_calls: [], file_changes: [],
}

const completed: Run = {
  id: 'run-completed', session_id: 'session-1', prompt: 'Inspect the project', model: 'fake',
  prompt_version: 'v1', status: 'completed', max_steps: 20, step_count: 1,
  final_response: 'Saved answer', error_text: null,
  created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:01Z',
  finished_at: '2026-09-01T00:00:01Z', messages: [], tool_calls: [], file_changes: [],
}

const completedWithEmptyResponse: Run = {
  id: 'run-empty-completed', session_id: 'session-1', prompt: 'Inspect the project', model: 'fake',
  prompt_version: 'v1', status: 'completed', max_steps: 20, step_count: 1,
  final_response: '', error_text: null,
  created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:01Z',
  finished_at: '2026-09-01T00:00:01Z', messages: [], tool_calls: [], file_changes: [],
}

test('renders task evidence and final response', () => {
  const run: Run = {
    id: 'run-1', session_id: 'session-1', prompt: 'Inspect the project', model: 'fake',
    prompt_version: 'v1', status: 'completed', max_steps: 20, step_count: 1,
    final_response: 'Everything is ready.', error_text: null,
    created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:01Z',
    finished_at: '2026-09-01T00:00:01Z', messages: [], tool_calls: [], file_changes: [],
  }
  render(Timeline, { props: {
    title: 'Demo', history: [run], selected: run, draft: '', streaming: false,
    cancelling: false, error: null,
  } })
  expect(screen.getByText('Inspect the project')).toBeTruthy()
  expect(screen.getByText('Everything is ready.')).toBeTruthy()
  expect(screen.getByText('已完成')).toBeTruthy()
})

test('renders a live assistant draft with generation status', () => {
  render(Timeline, { props: {
    title: 'Demo', history: [running], selected: running,
    draft: 'Working on it', streaming: true, cancelling: false, error: null,
  } })
  expect(screen.getByText('Working on it')).toBeTruthy()
  expect(screen.getByText('正在生成')).toBeTruthy()
})

test('prefers the durable final response over a stale draft', () => {
  render(Timeline, { props: {
    title: 'Demo', history: [completed], selected: completed,
    draft: 'Stale draft', streaming: false, cancelling: false, error: null,
  } })
  expect(screen.getByText('Saved answer')).toBeTruthy()
  expect(screen.queryByText('Stale draft')).toBeNull()
})

test('prefers an empty durable final response over a stale draft', () => {
  render(Timeline, { props: {
    title: 'Demo', history: [completedWithEmptyResponse], selected: completedWithEmptyResponse,
    draft: 'Stale draft', streaming: false, cancelling: false, error: null,
  } })
  expect(screen.getByText('Agent')).toBeTruthy()
  expect(screen.queryByText('Stale draft')).toBeNull()
})

test('renders the whole session as one chronological conversation with a collapsed tool chain', async () => {
  const user = userEvent.setup()
  const earlier: Run = {
    ...completed,
    id: 'run-earlier',
    prompt: 'First requirement',
    final_response: 'First final reply',
    created_at: '2026-09-01T00:00:00Z',
    tool_calls: [
      {
        id: 'tool-1', run_id: 'run-earlier', provider_call_id: 'call-1', name: 'read_file',
        arguments_json: '{"path":"a.py"}', status: 'succeeded', result_json: '{"ok":true}',
        duration_ms: 4, started_at: '2026-09-01T00:00:01Z', finished_at: '2026-09-01T00:00:02Z',
      },
      {
        id: 'tool-2', run_id: 'run-earlier', provider_call_id: 'call-2', name: 'write_file',
        arguments_json: '{"path":"a.py"}', status: 'succeeded', result_json: '{"ok":true}',
        duration_ms: 8, started_at: '2026-09-01T00:00:03Z', finished_at: '2026-09-01T00:00:04Z',
      },
    ],
  }
  const later: Run = {
    ...completed,
    id: 'run-later',
    prompt: 'Second requirement',
    final_response: 'Second final reply',
    created_at: '2026-09-01T01:00:00Z',
  }

  const view = render(Timeline, { props: {
    title: 'Demo', history: [later, earlier], selected: later, draft: '', streaming: false,
    cancelling: false, error: null,
  } })

  const messages = [...view.container.querySelectorAll('.message p')]
    .map(node => node.textContent)
  expect(messages).toEqual([
    'First requirement', 'First final reply', 'Second requirement', 'Second final reply',
  ])
  expect(screen.queryByRole('navigation', { name: '运行历史' })).toBeNull()

  const summary = screen.getByText('工具调用 · 2 步')
  const chain = summary.closest('details') as HTMLDetailsElement
  expect(chain.open).toBe(false)
  await user.click(summary)
  expect(chain.open).toBe(true)
})

test('keeps following the conversation when a new run is appended at the bottom', async () => {
  const view = render(Timeline, { props: {
    title: 'Demo', history: [completed], selected: completed, draft: '', streaming: false,
    cancelling: false, error: null,
  } })
  const timeline = screen.getByRole('main', { name: '执行过程' })
  Object.defineProperties(timeline, {
    scrollHeight: { configurable: true, value: 1000 },
    clientHeight: { configurable: true, value: 400 },
    scrollTop: { configurable: true, value: 600, writable: true },
  })
  const nextRun: Run = {
    ...completed,
    id: 'run-next',
    prompt: 'Continue the task',
    final_response: 'Done again',
    created_at: '2026-09-01T01:00:00Z',
  }

  await view.rerender({
    title: 'Demo', history: [nextRun, completed], selected: nextRun, draft: '', streaming: false,
    cancelling: false, error: null,
  })

  await waitFor(() => expect(timeline.scrollTop).toBe(1000))
})

test('does not pull the reader away from earlier messages after they scroll up', async () => {
  const view = render(Timeline, { props: {
    title: 'Demo', history: [running], selected: running, draft: 'Beginning', streaming: true,
    cancelling: false, error: null,
  } })
  const timeline = screen.getByRole('main', { name: '执行过程' })
  Object.defineProperties(timeline, {
    scrollHeight: { configurable: true, value: 1000 },
    clientHeight: { configurable: true, value: 400 },
    scrollTop: { configurable: true, value: 100, writable: true },
  })
  await fireEvent.scroll(timeline)

  await view.rerender({
    title: 'Demo', history: [running], selected: running,
    draft: 'Beginning and continuing', streaming: true, cancelling: false, error: null,
  })

  await nextTick()
  await nextTick()
  expect(timeline.scrollTop).toBe(100)
})

test('opens a different session at its latest conversation', async () => {
  const view = render(Timeline, { props: {
    sessionId: 'session-a', title: 'Task A', history: [running], selected: running,
    draft: 'Beginning', streaming: true, cancelling: false, error: null,
  } })
  const timeline = screen.getByRole('main', { name: '执行过程' })
  Object.defineProperties(timeline, {
    scrollHeight: { configurable: true, value: 1000 },
    clientHeight: { configurable: true, value: 400 },
    scrollTop: { configurable: true, value: 100, writable: true },
  })
  await fireEvent.scroll(timeline)

  await view.rerender({
    sessionId: 'session-b', title: 'Task B', history: [completed], selected: completed,
    draft: '', streaming: false, cancelling: false, error: null,
  })

  await nextTick()
  await nextTick()
  expect(timeline.scrollTop).toBe(1000)
})
