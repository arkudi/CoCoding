import { render, screen } from '@testing-library/vue'
import { expect, test } from 'vitest'
import MarkdownContent from './MarkdownContent.vue'

test('renders headings, lists, and code as Markdown', () => {
  render(MarkdownContent, {
    props: { content: '## 完成\n\n- 修改文件\n- 运行测试\n\n`npm test`' },
  })

  expect(screen.getByRole('heading', { name: '完成', level: 2 })).toBeTruthy()
  expect(screen.getAllByRole('listitem')).toHaveLength(2)
  expect(screen.getByText('npm test').tagName).toBe('CODE')
})

test('removes unsafe HTML from model output', () => {
  const view = render(MarkdownContent, {
    props: { content: '<img src="x" onerror="alert(1)"><script>alert(2)</script>' },
  })

  expect(view.container.querySelector('script')).toBeNull()
  expect(view.container.querySelector('img')?.hasAttribute('onerror')).toBe(false)
})

