import { render, screen } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createPinia } from 'pinia'
import { expect, test, vi } from 'vitest'
import App from './App.vue'

test('renders the three primary work areas', () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify([]), { status: 200 }),
  ))

  render(App, { global: { plugins: [createPinia()] } })

  expect(screen.getByRole('complementary', { name: '任务' })).toBeTruthy()
  expect(screen.getByRole('main', { name: '执行过程' })).toBeTruthy()
  expect(screen.getByRole('complementary', { name: '工作区' })).toBeTruthy()
})

test('creates a session and shows it in history', async () => {
  const user = userEvent.setup()
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([]), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      id: '3d66a599-d202-4c8f-b3c3-7dc45888d277',
      title: 'Fix calculator',
      workspace_path: 'F:/demo/calculator',
      status: 'idle',
      created_at: '2026-08-28T10:00:00Z',
      updated_at: '2026-08-28T10:00:00Z',
    }), { status: 201 }))
  vi.stubGlobal('fetch', fetchMock)

  render(App, { global: { plugins: [createPinia()] } })
  await user.click(screen.getByRole('button', { name: '新建任务' }))
  await user.type(screen.getByLabelText('任务名称'), 'Fix calculator')
  await user.type(screen.getByLabelText('工作区路径'), 'F:/demo/calculator')
  await user.click(screen.getByRole('button', { name: '创建' }))

  expect(await screen.findByRole('button', { name: 'Fix calculator' })).toBeTruthy()
  expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      title: 'Fix calculator',
      workspace_path: 'F:/demo/calculator',
    }),
  })
})

test('shows a failed history load in the normal sidebar state', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: '无法加载任务历史' }), { status: 503 }),
  ))

  render(App, { global: { plugins: [createPinia()] } })

  expect((await screen.findByRole('alert')).textContent).toContain('无法加载任务历史')
})
