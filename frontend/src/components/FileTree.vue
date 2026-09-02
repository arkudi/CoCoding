<script setup lang="ts">
import { computed, ref, watch } from 'vue'

interface TreeNode {
  name: string
  path: string
  kind: 'folder' | 'file'
  children: TreeNode[]
}

interface VisibleNode extends TreeNode { depth: number }

const props = defineProps<{ files: string[], selectedPath: string | null }>()
const emit = defineEmits<{ select: [path: string] }>()
const expanded = ref(new Set<string>())

const roots = computed<TreeNode[]>(() => {
  const root: TreeNode = { name: '', path: '', kind: 'folder', children: [] }
  const folders = new Map<string, TreeNode>([['', root]])
  for (const path of props.files) {
    const parts = path.split('/').filter(Boolean)
    let parent = root
    for (let index = 0; index < parts.length; index += 1) {
      const currentPath = parts.slice(0, index + 1).join('/')
      if (index === parts.length - 1) {
        parent.children.push({ name: parts[index], path, kind: 'file', children: [] })
      } else {
        let folder = folders.get(currentPath)
        if (!folder) {
          folder = { name: parts[index], path: currentPath, kind: 'folder', children: [] }
          folders.set(currentPath, folder)
          parent.children.push(folder)
        }
        parent = folder
      }
    }
  }
  const sort = (nodes: TreeNode[]) => {
    nodes.sort((left, right) => {
      if (left.kind !== right.kind) return left.kind === 'folder' ? -1 : 1
      return left.name.localeCompare(right.name, undefined, { sensitivity: 'base' })
    })
    nodes.forEach(node => sort(node.children))
  }
  sort(root.children)
  return root.children
})

const visibleNodes = computed<VisibleNode[]>(() => {
  const result: VisibleNode[] = []
  const visit = (nodes: TreeNode[], depth: number) => {
    for (const node of nodes) {
      result.push({ ...node, depth })
      if (node.kind === 'folder' && expanded.value.has(node.path)) {
        visit(node.children, depth + 1)
      }
    }
  }
  visit(roots.value, 0)
  return result
})

function activate(node: TreeNode) {
  if (node.kind === 'file') {
    emit('select', node.path)
    return
  }
  const next = new Set(expanded.value)
  if (next.has(node.path)) next.delete(node.path)
  else next.add(node.path)
  expanded.value = next
}

watch(() => props.selectedPath, (path) => {
  if (!path) return
  const parts = path.split('/')
  const next = new Set(expanded.value)
  for (let index = 1; index < parts.length; index += 1) {
    next.add(parts.slice(0, index).join('/'))
  }
  expanded.value = next
}, { immediate: true })
</script>

<template>
  <nav class="file-tree" aria-label="工作区文件" role="tree">
    <button
      v-for="node in visibleNodes" :key="`${node.kind}:${node.path}`" type="button"
      role="treeitem"
      :aria-label="node.kind === 'folder' ? `${node.name} 文件夹` : node.path"
      :aria-expanded="node.kind === 'folder' ? expanded.has(node.path) : undefined"
      :class="['tree-row', node.kind, { selected: node.path === selectedPath }]"
      :style="{ '--tree-depth': node.depth }"
      @click="activate(node)"
    >
      <span v-if="node.kind === 'folder'" class="disclosure" aria-hidden="true">›</span>
      <span v-else aria-hidden="true" />
      <span class="node-icon" aria-hidden="true" />
      <span class="node-name">{{ node.name }}</span>
    </button>
  </nav>
</template>
