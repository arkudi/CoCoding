// @vitest-environment node

import { expect, test } from 'vitest'
import type { UserConfig } from 'vite'
import viteConfig from './vite.config'

test('forwards WebSocket upgrades for API event streams', () => {
  const config = viteConfig as UserConfig

  expect(config.server?.proxy?.['/api']).toMatchObject({
    target: 'http://127.0.0.1:8000',
    ws: true,
  })
})
