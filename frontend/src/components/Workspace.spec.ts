import { render, screen } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { expect, test } from 'vitest'
import Workspace from './Workspace.vue'

test('switches to Diff and renders unified file evidence', async () => {
  const user = userEvent.setup()
  render(Workspace, { props: {
    workspacePath: 'F:/demo/calculator', files: [], selectedPath: null, preview: null, error: null,
    loading: false, syncing: false, truncated: false,
    fileChanges: [{
      id: 'change-1', run_id: 'run-1', relative_path: 'src/main.py', operation: 'modified',
      before_hash: 'a', after_hash: 'b', unified_diff: '-old\n+new\n',
      created_at: '2026-09-01T00:00:00Z',
    }],
  } })
  await user.click(screen.getByRole('tab', { name: 'Diff' }))
  await user.click(screen.getByRole('button', { name: 'src/main.py' }))
  expect(screen.getByText(/-old/)).toBeTruthy()
  expect(screen.getByRole('tab', { name: 'Diff' }).getAttribute('aria-selected')).toBe('true')
})

test('shows the active workspace path above the file view', () => {
  render(Workspace, { props: {
    workspacePath: 'F:/Codes/agent', files: [], selectedPath: null, preview: null, error: null,
    loading: false, syncing: false, truncated: false, fileChanges: [],
  } })

  expect(screen.getByText('当前路径')).toBeTruthy()
  expect(screen.getByText('F:/Codes/agent')).toBeTruthy()
  expect(screen.getByText('F:/Codes/agent').closest('.workspace-path')?.getAttribute('title')).toBe('F:/Codes/agent')
  expect(screen.queryByText('实时同步')).toBeNull()
  expect(screen.queryByText('同步中')).toBeNull()
})
