import { expect, test, vi } from 'vitest'
import { listWorkspaceFiles, readWorkspaceFile } from './workspace'

test('loads files and encodes the selected relative path', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ files: ['src/main.py'], truncated: false })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ path: 'src/a b.py', content: 'x', size: 1 })))
  vi.stubGlobal('fetch', fetchMock)

  await listWorkspaceFiles('session-1')
  await readWorkspaceFile('session-1', 'src/a b.py')

  expect(fetchMock.mock.calls[1][0]).toBe(
    '/api/sessions/session-1/files/content?path=src%2Fa+b.py',
  )
})
