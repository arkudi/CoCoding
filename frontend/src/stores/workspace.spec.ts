import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, test, vi } from 'vitest'
import { useWorkspaceStore } from './workspace'

const api = vi.hoisted(() => ({ listWorkspaceFiles: vi.fn(), readWorkspaceFile: vi.fn() }))
vi.mock('@/api/workspace', () => api)

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

test('loads and previews a selected workspace file', async () => {
  api.listWorkspaceFiles.mockResolvedValue({ files: ['src/main.py'], truncated: false })
  api.readWorkspaceFile.mockResolvedValue({ path: 'src/main.py', content: 'print(1)', size: 8 })
  const store = useWorkspaceStore()

  await store.loadFiles('session-1')
  await store.selectFile('session-1', 'src/main.py')

  expect(store.files).toEqual(['src/main.py'])
  expect(store.preview?.content).toBe('print(1)')
})

test('reset prevents a stale preview from being applied', async () => {
  let resolve!: (value: { path: string, content: string, size: number }) => void
  api.readWorkspaceFile.mockReturnValue(new Promise(next => { resolve = next }))
  const store = useWorkspaceStore()
  const loading = store.selectFile('session-1', 'old.py')
  store.reset()
  resolve({ path: 'old.py', content: 'stale', size: 5 })
  await loading
  expect(store.preview).toBeNull()
})
