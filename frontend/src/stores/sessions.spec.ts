import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, test, vi } from 'vitest'
import { useSessionsStore } from './sessions'

beforeEach(() => setActivePinia(createPinia()))

test('keeps a newly created session when an earlier history load resolves late', async () => {
  let resolveList: (response: Response) => void
  const slowList = new Promise<Response>((resolve) => {
    resolveList = resolve
  })
  const created = {
    id: '3d66a599-d202-4c8f-b3c3-7dc45888d277',
    title: 'Fix calculator',
    workspace_path: 'F:/demo/calculator',
    status: 'idle' as const,
    created_at: '2026-08-28T10:00:00Z',
    updated_at: '2026-08-28T10:00:00Z',
  }
  const existing = {
    id: 'a730a9eb-3691-4622-9614-34976f0f2ad7',
    title: 'Existing task',
    workspace_path: 'F:/demo/existing',
    status: 'idle' as const,
    created_at: '2026-08-27T10:00:00Z',
    updated_at: '2026-08-27T10:00:00Z',
  }
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(slowList)
    .mockResolvedValueOnce(new Response(JSON.stringify(created), { status: 201 })))
  const sessions = useSessionsStore()

  const loading = sessions.load()
  await sessions.create({ title: created.title, workspace_path: created.workspace_path })
  resolveList!(new Response(JSON.stringify([created, existing]), { status: 200 }))
  await loading

  expect(sessions.items).toEqual([created, existing])
  expect(sessions.current_id).toBe(created.id)
})

test('removes the current session and selects its neighbor', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })))
  const sessions = useSessionsStore()
  sessions.items = [
    {
      id: 'first', title: 'First task', workspace_path: 'F:/first', status: 'idle',
      created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
    },
    {
      id: 'second', title: 'Second task', workspace_path: 'F:/second', status: 'completed',
      created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
    },
  ]
  sessions.current_id = 'first'

  expect(await sessions.remove('first')).toBe(true)

  expect(sessions.items.map(item => item.id)).toEqual(['second'])
  expect(sessions.current_id).toBe('second')
  expect(sessions.deleting_ids).toEqual([])
})

test('keeps the session and reports an API deletion error', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
    detail: '正在执行的任务不能删除，请先取消任务',
  }), { status: 409 })))
  const sessions = useSessionsStore()
  sessions.items = [{
    id: 'running', title: 'Running task', workspace_path: 'F:/work', status: 'running',
    created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
  }]
  sessions.current_id = 'running'

  expect(await sessions.remove('running')).toBe(false)

  expect(sessions.items).toHaveLength(1)
  expect(sessions.current_id).toBe('running')
  expect(sessions.error).toBe('正在执行的任务不能删除，请先取消任务')
})

test('does not restore a deleted session when an older history load finishes', async () => {
  let resolveList: (response: Response) => void
  const slowList = new Promise<Response>((resolve) => {
    resolveList = resolve
  })
  const deleted = {
    id: 'deleted', title: 'Deleted task', workspace_path: 'F:/deleted', status: 'idle' as const,
    created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
  }
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(slowList)
    .mockResolvedValueOnce(new Response(null, { status: 204 })))
  const sessions = useSessionsStore()
  sessions.items = [deleted]
  sessions.current_id = deleted.id

  const loading = sessions.load()
  await sessions.remove(deleted.id)
  resolveList!(new Response(JSON.stringify([deleted]), { status: 200 }))
  await loading

  expect(sessions.items).toEqual([])
  expect(sessions.current_id).toBeNull()
  expect(sessions.loading).toBe(false)
})

test('opens the workspace picker at the current task directory', async () => {
  const current = {
    id: 'current', title: 'Current task', workspace_path: 'F:/Codes/current', status: 'idle' as const,
    created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
  }
  const created = {
    id: 'next', title: 'Next task', workspace_path: 'F:/Codes/next', status: 'idle' as const,
    created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
  }
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ path: created.workspace_path })))
    .mockResolvedValueOnce(new Response(JSON.stringify(created), { status: 201 }))
  vi.stubGlobal('fetch', fetchMock)
  const sessions = useSessionsStore()
  sessions.items = [current]
  sessions.current_id = current.id

  await sessions.createFromPicker()

  expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/sessions/select-workspace', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ initial_path: current.workspace_path }),
  })
  expect(sessions.current_id).toBe(created.id)
})
