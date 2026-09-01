import { render, screen } from '@testing-library/vue'
import { expect, test } from 'vitest'
import Timeline from './Timeline.vue'
import type { Run } from '@/types/run'

test('renders task evidence and final response', () => {
  const run: Run = {
    id: 'run-1', session_id: 'session-1', prompt: 'Inspect the project', model: 'fake',
    prompt_version: 'v1', status: 'completed', max_steps: 20, step_count: 1,
    final_response: 'Everything is ready.', error_text: null,
    created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:01Z',
    finished_at: '2026-09-01T00:00:01Z', messages: [], tool_calls: [], file_changes: [],
  }
  render(Timeline, { props: {
    title: 'Demo', history: [run], selected: run, cancelling: false, error: null,
  } })
  expect(screen.getByText('Inspect the project')).toBeTruthy()
  expect(screen.getByText('Everything is ready.')).toBeTruthy()
  expect(screen.getByText('已完成')).toBeTruthy()
})
