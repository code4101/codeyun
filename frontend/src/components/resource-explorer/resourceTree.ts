/** 数据提供方负责加载和操作资源，树组件只负责展示与交互。ID 在整棵树内唯一。 */
export interface ResourceNode {
  id: string
  name: string
  kind: 'directory' | 'file'
  children?: ResourceNode[]
  expanded?: boolean
  loaded?: boolean
  loading?: boolean
  error?: string
  /** 书架、工作区根等语义边界可以禁止路径压缩。 */
  compact?: boolean
}
export interface ResourceRow {
  id: string
  node: ResourceNode
  path: ResourceNode[]
  label: string
  depth: number
  parentId?: string
  expanded: boolean
}

/** 单子目录链压为一行；文件、分叉、未加载及出错的目录都不会被跳过。 */
export function resourceRows(nodes: readonly ResourceNode[], compact = true): ResourceRow[] {
  const result: ResourceRow[] = []
  const stack = [...nodes].reverse().map(node => ({ node, depth: 0, parentId: undefined as string | undefined }))
  while (stack.length) {
    const entry = stack.pop()!
    const path = [entry.node]
    let node = entry.node
    while (compact && node.kind === 'directory' && node.compact !== false && node.loaded !== false && !node.loading && !node.error && node.children?.length === 1) {
      const child = node.children[0]!
      if (child.kind !== 'directory' || child.compact === false || path.includes(child)) break
      path.push(child)
      node = child
    }
    const row: ResourceRow = { id: entry.node.id, node, path, label: path.map(item => item.name).join(' / '), depth: entry.depth, parentId: entry.parentId, expanded: Boolean(entry.node.expanded) }
    result.push(row)
    if (row.expanded && node.kind === 'directory' && !node.loading && !node.error) {
      for (const child of [...(node.children ?? [])].reverse()) stack.push({ node: child, depth: entry.depth + 1, parentId: row.id })
    }
  }
  return result
}
