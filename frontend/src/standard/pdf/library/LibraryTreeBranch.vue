<script setup lang="ts">
import { computed } from 'vue'
import ResourceExplorer from '@/components/resource-explorer/ResourceExplorer.vue'
import type { ResourceNode } from '@/components/resource-explorer/resourceTree'
import { useLibraryTree, type LibraryTreeNode } from './useLibraryTree'
import { useReaderWorkspace } from './useReaderWorkspace'
import { readerFileTitle } from './readerFileTitle'
const props = defineProps<{ nodes: LibraryTreeNode[] }>()
const tree = useLibraryTree()
const workspace = useReaderWorkspace()
// 图书馆只适配业务数据与行为，展开、路径压缩、键盘和样式统一由资源树处理。
const resourceData = computed(() => {
  const lookup = new Map<string, LibraryTreeNode>()
  function adapt(nodes: LibraryTreeNode[], root = false): ResourceNode[] {
    return nodes.map(node => {
      lookup.set(node.id, node)
      return {
        id: node.id, name: readerFileTitle(node.title, '', node.tab?.kind === 'pdf' ? 'pdf' : ''),
        kind: node.tab ? 'file' : 'directory', compact: !root,
        expanded: node.expanded, loaded: node.loaded ?? false, loading: node.loading, error: node.error,
        children: node.children ? adapt(node.children) : undefined,
      }
    })
  }
  return { nodes: adapt(props.nodes, true), lookup }
})
function open(node: ResourceNode) {
  const tab = resourceData.value.lookup.get(node.id)?.tab
  if (tab) void workspace.open(tab).catch(() => undefined)
}
function toggle(path: ResourceNode[], expanded: boolean) {
  const nodes = path.map(node => resourceData.value.lookup.get(node.id)).filter((node): node is LibraryTreeNode => Boolean(node))
  for (const node of nodes) node.expanded = expanded
  const tail = nodes[nodes.length - 1]
  if (expanded && tail) void tree.expand(tail)
}
function retry(node: ResourceNode) {
  const source = resourceData.value.lookup.get(node.id)
  if (source) void tree.expand(source)
}
</script>
<template>
  <ResourceExplorer class="library-resources" :nodes="resourceData.nodes" :selected-id="workspace.state.active" label="图书馆" @open="open" @toggle="toggle" @retry="retry" />
</template>
<style scoped>
.library-resources { --resource-tree-text: var(--reader-text); --resource-tree-hover: var(--reader-hover); --resource-tree-active: var(--reader-active); --resource-tree-accent: var(--reader-active-text); --resource-tree-muted: var(--reader-muted); }
</style>
