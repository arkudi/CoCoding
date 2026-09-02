import { render, screen } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { expect, test } from 'vitest'
import FileTree from './FileTree.vue'

test('renders folders collapsed and expands them on click', async () => {
  const user = userEvent.setup()
  render(FileTree, { props: { files: ['src/main.py', 'src/lib/util.py', 'README.md'], selectedPath: null } })

  const folder = screen.getByRole('treeitem', { name: 'src 文件夹' })
  expect(folder.getAttribute('aria-expanded')).toBe('false')
  expect(screen.queryByRole('treeitem', { name: 'src/main.py' })).toBeNull()
  expect(screen.getByRole('treeitem', { name: 'README.md' })).toBeTruthy()

  await user.click(folder)
  expect(folder.getAttribute('aria-expanded')).toBe('true')
  expect(screen.getByRole('treeitem', { name: 'src/main.py' })).toBeTruthy()
  expect(screen.getByRole('treeitem', { name: 'lib 文件夹' })).toBeTruthy()
})
