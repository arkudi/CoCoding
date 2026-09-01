import { render, screen } from '@testing-library/vue'
import { expect, test } from 'vitest'
import ToolCallCard from './ToolCallCard.vue'

test('renders tool evidence as text', () => {
  render(ToolCallCard, { props: { call: {
    id: 'tool-1', run_id: 'run-1', provider_call_id: 'call-1', name: 'read_file',
    arguments_json: '{"path":"a.py"}', status: 'succeeded', result_json: '{"ok":true}',
    duration_ms: 4, started_at: '2026-09-01T00:00:00Z', finished_at: '2026-09-01T00:00:01Z',
  } } })
  expect(screen.getByText('read_file')).toBeTruthy()
  expect(screen.getByText(/a.py/)).toBeTruthy()
})
