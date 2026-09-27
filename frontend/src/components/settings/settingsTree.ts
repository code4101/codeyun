/** 配置描述与存储解耦：读取和修改直接调用功能自身的状态/API，不复制配置值。 */
interface SettingBase { id: string; label: string; description?: string }
export type SettingNode =
  | (SettingBase & { kind: 'group'; children: SettingNode[] })
  | (SettingBase & { kind: 'boolean'; read: () => boolean; write: (value: boolean) => void })
  | (SettingBase & { kind: 'number'; min: number; max: number; step?: number; read: () => number; write: (value: number) => void })
  | (SettingBase & { kind: 'select'; options: readonly { value: string; label: string }[]; read: () => string; write: (value: string) => void })
  | (SettingBase & { kind: 'action'; run: () => void })

/** 命中分类时保留整棵子树，命中叶子时保留其祖先路径。 */
export function filterSettings(nodes: readonly SettingNode[], query: string): SettingNode[] {
  const term = query.trim().toLocaleLowerCase()
  if (!term) return [...nodes]
  return nodes.flatMap(node => {
    if (`${node.label} ${node.description ?? ''}`.toLocaleLowerCase().includes(term)) return [node]
    if (node.kind !== 'group') return []
    const children = filterSettings(node.children, term)
    return children.length ? [{ ...node, children }] : []
  })
}
