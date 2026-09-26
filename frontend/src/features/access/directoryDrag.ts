import type { InjectionKey, Ref } from 'vue'
import type { FeatureAccessTreeItem } from '@/api/access'

export type DirectoryDropPosition = 'before' | 'after' | 'inside'
export interface DirectoryDrop { key: string; targetKey: string; position: DirectoryDropPosition }
export interface DirectoryDragContext {
  source: Ref<FeatureAccessTreeItem | null>
  hover: Ref<DirectoryDrop | null>
  busy: Ref<boolean>
  move: (drop: DirectoryDrop) => Promise<void>
}
export const directoryDragKey: InjectionKey<DirectoryDragContext> = Symbol('directory-drag')

export function canDropDirectory(source: FeatureAccessTreeItem, target: FeatureAccessTreeItem, position: DirectoryDropPosition): boolean {
  const contains = (item: FeatureAccessTreeItem): boolean => item.key === target.key || item.children.some(contains)
  // A page can also own subpages; node_type describes its content, not a
  // restriction on hierarchy. Only self/descendant moves would create cycles.
  return !contains(source)
}

export function directoryDropPosition(ratio: number, target: FeatureAccessTreeItem): DirectoryDropPosition {
  if (ratio >= 0.25 && ratio <= 0.75) return 'inside'
  return ratio < 0.5 ? 'before' : 'after'
}
