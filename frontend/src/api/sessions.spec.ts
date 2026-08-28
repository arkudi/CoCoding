import { expect, test, vi } from 'vitest'
import { listSessions } from './sessions'

test('formats FastAPI validation detail arrays as readable field messages', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
    detail: [{
      type: 'string_too_short',
      loc: ['body', 'title'],
      msg: 'String should have at least 1 character',
      input: '',
    }],
  }), { status: 422 })))

  await expect(listSessions()).rejects.toMatchObject({
    status: 422,
    detail: 'title: String should have at least 1 character',
  })
})
