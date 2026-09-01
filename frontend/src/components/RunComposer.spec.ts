import { render, screen } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { expect, test } from 'vitest'
import RunComposer from './RunComposer.vue'

test('submits a trimmed task prompt', async () => {
  const user = userEvent.setup()
  const view = render(RunComposer, { props: { running: false, cancelling: false } })
  await user.type(screen.getByLabelText('任务描述'), '  inspect project  ')
  await user.click(screen.getByRole('button', { name: '运行任务' }))
  expect(view.emitted().submit?.[0]).toEqual([{ prompt: 'inspect project', max_steps: 20 }])
})

test('shows the cooperative cancelling state', () => {
  render(RunComposer, { props: { running: true, cancelling: true } })
  const button = screen.getByRole('button', { name: '正在取消' }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
})
