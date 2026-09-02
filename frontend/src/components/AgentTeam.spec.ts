import { render, screen } from '@testing-library/vue'
import { expect, test } from 'vitest'
import AgentTeam from './AgentTeam.vue'
import type { AgentExecution } from '@/types/run'

const manager: AgentExecution = {
  id: 'manager-1', run_id: 'run-1', parent_execution_id: null,
  role: 'manager', task: 'Coordinate the task', status: 'running', step_count: 2,
  final_result_json: null, started_at: '2026-09-02T00:00:00Z', finished_at: null,
}

test('renders parent and worker state with the worker summary', () => {
  render(AgentTeam, { props: { executions: [
    manager,
    {
      ...manager,
      id: 'worker-1', parent_execution_id: manager.id, role: 'implementer',
      task: 'Implement the change', status: 'completed', step_count: 3,
      final_result_json: JSON.stringify({ result: { summary: 'Changed the target file.' } }),
      finished_at: '2026-09-02T00:00:03Z',
    },
  ], tasks: [{
    id: 'task-1', run_id: 'run-1', execution_id: 'worker-1', role: 'implementer',
    description: 'Implement the change', expected_output: 'A patch', depends_on: [],
    status: 'completed', result_json: null, started_at: manager.started_at,
    created_at: manager.started_at, finished_at: '2026-09-02T00:00:03Z',
  }] } })

  expect(screen.getByRole('region', { name: '智能体协作' })).toBeTruthy()
  expect(screen.getByText('Manager')).toBeTruthy()
  expect(screen.getAllByText('Implementer')).toHaveLength(2)
  expect(screen.getByText('Changed the target file.')).toBeTruthy()
  expect(screen.getByText('3 步')).toBeTruthy()
  expect(screen.getByText('Task DAG')).toBeTruthy()
})
