import { render, screen } from '@testing-library/vue'
import { expect, test } from 'vitest'
import FileTree from './FileTree.vue'

test('renders relative workspace paths', () => {
  render(FileTree, { props: { files: ['src/main.py', 'README.md'], selectedPath: null } })
  expect(screen.getByRole('button', { name: 'src/main.py' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'README.md' })).toBeTruthy()
})
