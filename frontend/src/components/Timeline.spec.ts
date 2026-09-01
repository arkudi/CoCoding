import { render, screen } from '@testing-library/vue'
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
