import { render, screen } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { expect, test } from 'vitest'
import App from './App.vue'

test('renders the three primary work areas', () => {
  render(App, { global: { plugins: [createPinia()] } })

  expect(screen.getByRole('complementary', { name: '任务' })).toBeTruthy()
  expect(screen.getByRole('main', { name: '执行过程' })).toBeTruthy()
  expect(screen.getByRole('complementary', { name: '工作区' })).toBeTruthy()
})
