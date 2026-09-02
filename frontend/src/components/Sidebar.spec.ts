import { render, screen } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createPinia, setActivePinia } from 'pinia'
import { expect, test, vi } from 'vitest'
import Sidebar from './Sidebar.vue'
import { useSessionsStore } from '@/stores/sessions'

function renderSidebar(status: 'idle' | 'running' = 'idle') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const sessions = useSessionsStore()
  sessions.items = [{
    id: 'session-1', title: '演示任务', workspace_path: 'F:/demo', status,
    created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
  }]
  sessions.current_id = 'session-1'
  return { sessions, view: render(Sidebar, { global: { plugins: [pinia] } }) }
}

test('asks for inline confirmation before deleting a task record', async () => {
  const user = userEvent.setup()
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
  vi.stubGlobal('fetch', fetchMock)
  const { sessions } = renderSidebar()

  await user.click(screen.getByRole('button', { name: '删除任务 演示任务' }))
  expect(screen.getByText('只删除任务记录？')).toBeTruthy()
  expect(fetchMock).not.toHaveBeenCalled()

  await user.click(screen.getByRole('button', { name: '确认' }))
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(sessions.items).toEqual([])
  expect(screen.getByText('还没有任务记录')).toBeTruthy()
})

test('disables deletion while a task is running', () => {
  renderSidebar('running')

  expect(screen.getByRole('button', { name: '删除任务 演示任务' }).hasAttribute('disabled')).toBe(true)
})
