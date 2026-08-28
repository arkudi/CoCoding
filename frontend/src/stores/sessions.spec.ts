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
