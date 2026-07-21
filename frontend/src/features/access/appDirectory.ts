import { buildPermissionRegistryTree } from './permissionRegistry'
import type {
  FeatureDirectoryMenuIcon,
  FeatureDirectoryMenuItemDefinition,
  FeaturePermissionTreeNode,
} from './permissionRegistryTypes'
import { pageRegistry } from '@/router/pageRegistry'

export interface AppDirectoryMenuItem {
  path: string
  title: string
}

export interface AppDirectoryNode {
  key: string
  title: string
  icon?: FeatureDirectoryMenuIcon
  slot: 'main' | 'footer'
  menuItems: AppDirectoryMenuItem[]
  menuItemsInline: boolean
  children: AppDirectoryNode[]
}

export interface AppDirectoryVisibilityContext {
  isAllowed: (permissionKey: string) => boolean
  isAuthenticated: boolean
  isAdmin: boolean
}

function getRawMenuItems(node: FeaturePermissionTreeNode): FeatureDirectoryMenuItemDefinition[] {
  return node.menu_items ?? node.menu_paths.map((path) => ({ path }))
}

function canShowMenuPath(
  path: string,
  context: Pick<AppDirectoryVisibilityContext, 'isAuthenticated' | 'isAdmin'>,
) {
  const page = pageRegistry.find((item) => (item.menuPath ?? item.canonicalPath) === path)
  if (!page) {
    return true
  }
  if (page.requiresAdmin && !context.isAdmin) {
    return false
  }
  if (page.requiresAuth && !context.isAuthenticated) {
    return false
  }
  return true
}

function projectVisibleNode(
  node: FeaturePermissionTreeNode,
  context: AppDirectoryVisibilityContext,
): AppDirectoryNode | null {
  if (!context.isAllowed(node.key)) {
    return null
  }

  const menuItems = getRawMenuItems(node)
    .filter((item) => canShowMenuPath(item.path, context))
    .map((item) => ({
      path: item.path,
      title: item.title ?? node.title,
    }))
  const children = node.children
    .map((child) => projectVisibleNode(child, context))
    .filter((child): child is AppDirectoryNode => child !== null)

  if (menuItems.length === 0 && children.length === 0) {
    return null
  }

  return {
    key: node.key,
    title: node.title,
    icon: node.menu_icon,
    slot: node.menu_slot ?? 'main',
    menuItems,
    menuItemsInline: node.menu_items_inline ?? false,
    children,
  }
}

export function buildVisibleAppDirectory(context: AppDirectoryVisibilityContext) {
  return buildPermissionRegistryTree()
    .map((node) => projectVisibleNode(node, context))
    .filter((node): node is AppDirectoryNode => node !== null)
}

export function isDirectorySubmenu(node: AppDirectoryNode) {
  return !node.menuItemsInline && (node.children.length > 0 || node.menuItems.length > 1)
}

export function getDirectoryNodeEntryPath(node: AppDirectoryNode) {
  return node.menuItems[0]?.path ?? null
}

export function findDirectoryNodeByMenuPath(
  nodes: AppDirectoryNode[],
  menuPath: string,
): AppDirectoryNode | null {
  for (const node of nodes) {
    if (node.menuItems.some((item) => item.path === menuPath)) {
      return node
    }
    const childMatch = findDirectoryNodeByMenuPath(node.children, menuPath)
    if (childMatch) {
      return childMatch
    }
  }
  return null
}

export function getDirectoryOpenKeys(nodes: AppDirectoryNode[], menuPath: string) {
  const openKeys: string[] = []

  const visit = (node: AppDirectoryNode, ancestors: string[]): boolean => {
    const nodeIsSubmenu = isDirectorySubmenu(node)
    const nextAncestors = nodeIsSubmenu ? [...ancestors, node.key] : ancestors
    if (node.menuItems.some((item) => item.path === menuPath)) {
      openKeys.push(...ancestors)
      if (node.children.length > 0) {
        openKeys.push(node.key)
      }
      return true
    }
    for (const child of node.children) {
      if (visit(child, nextAncestors)) {
        return true
      }
    }
    return false
  }

  for (const node of nodes) {
    if (visit(node, [])) {
      break
    }
  }
  return Array.from(new Set(openKeys))
}
