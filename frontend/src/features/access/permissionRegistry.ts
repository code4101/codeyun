import rawPermissionRegistry from './permissionRegistry.json'
import { pluginPermissionNodes } from '@/plugins'
import type {
  FeaturePermissionNodeDefinition,
  FeaturePermissionRegistryDefinition,
  FeaturePermissionTreeNode,
} from './permissionRegistryTypes'

const basePermissionRegistry = rawPermissionRegistry as FeaturePermissionRegistryDefinition

function assertNoDuplicatePermissionKeys(nodes: FeaturePermissionNodeDefinition[]) {
  const seenKeys = new Set<string>()
  for (const node of nodes) {
    if (seenKeys.has(node.key)) {
      throw new Error(`权限节点 key 重复：${node.key}`)
    }
    seenKeys.add(node.key)
  }
}

function assertDirectoryMetadataConsistent(nodes: FeaturePermissionNodeDefinition[]) {
  for (const node of nodes) {
    if (!node.menu_items) {
      continue
    }
    const itemPaths = node.menu_items.map((item) => item.path)
    if (new Set(itemPaths).size !== itemPaths.length) {
      throw new Error(`目录节点存在重复菜单路径：${node.key}`)
    }
    if (
      itemPaths.length !== node.menu_paths.length
      || itemPaths.some((path, index) => path !== node.menu_paths[index])
    ) {
      throw new Error(`目录节点的 menu_items 与 menu_paths 不一致：${node.key}`)
    }
  }
}

const mergedPermissionNodes = [
  ...basePermissionRegistry.nodes,
  ...pluginPermissionNodes,
]

assertNoDuplicatePermissionKeys(mergedPermissionNodes)
assertDirectoryMetadataConsistent(mergedPermissionNodes)

export const permissionRegistry: FeaturePermissionRegistryDefinition = {
  ...basePermissionRegistry,
  nodes: mergedPermissionNodes,
}

export const permissionRegistryMap = new Map(
  permissionRegistry.nodes.map((node) => [node.key, node] as const),
)
export const routePathPermissionKeyMap = new Map<string, string>()
export const menuPathPermissionKeyMap = new Map<string, string>()

for (const node of permissionRegistry.nodes) {
  for (const routePath of node.route_paths) {
    routePathPermissionKeyMap.set(routePath, node.key)
  }
  for (const menuPath of node.menu_paths) {
    menuPathPermissionKeyMap.set(menuPath, node.key)
  }
}

export function buildPermissionRegistryTree() {
  const childrenMap = new Map<string | undefined, FeaturePermissionNodeDefinition[]>()

  for (const node of permissionRegistry.nodes) {
    const siblings = childrenMap.get(node.parent_key) ?? []
    siblings.push(node)
    childrenMap.set(node.parent_key, siblings)
  }

  const sortNodes = (left: FeaturePermissionNodeDefinition, right: FeaturePermissionNodeDefinition) =>
    left.sort_order - right.sort_order || left.title.localeCompare(right.title, 'zh-CN')

  const buildNode = (node: FeaturePermissionNodeDefinition): FeaturePermissionTreeNode => ({
    ...node,
    children: (childrenMap.get(node.key) ?? [])
      .slice()
      .sort(sortNodes)
      .map((child) => buildNode(child)),
  })

  return (childrenMap.get(undefined) ?? [])
    .slice()
    .sort(sortNodes)
    .map((node) => buildNode(node))
}

export function findPermissionKeyByRoutePath(path: string): string | null {
  return routePathPermissionKeyMap.get(path) ?? null
}

export function findPermissionKeyByMenuPath(path: string): string | null {
  return menuPathPermissionKeyMap.get(path) ?? null
}

export function getPermissionTitle(permissionKey: string): string | null {
  return permissionRegistryMap.get(permissionKey)?.title ?? null
}

export function requirePermissionTitle(permissionKey: string): string {
  const title = getPermissionTitle(permissionKey)
  if (!title) {
    throw new Error(`未找到权限标题定义：${permissionKey}`)
  }
  return title
}

export function getPermissionTitleByMenuPath(path: string): string | null {
  const permissionKey = findPermissionKeyByMenuPath(path)
  if (!permissionKey) {
    return null
  }
  return getPermissionTitle(permissionKey)
}

export function requirePermissionTitleByMenuPath(path: string): string {
  const title = getPermissionTitleByMenuPath(path)
  if (!title) {
    throw new Error(`未找到菜单标题定义：${path}`)
  }
  return title
}

export function buildPermissionTitleSegments(
  permissionKey: string,
  options?: {
    omitRoot?: boolean
  },
): string[] {
  const omitRoot = options?.omitRoot ?? true
  const segments: string[] = []
  let currentKey: string | undefined = permissionKey

  while (currentKey) {
    const node = permissionRegistryMap.get(currentKey)
    if (!node) {
      break
    }
    segments.unshift(node.title)
    currentKey = node.parent_key
  }

  if (omitRoot && segments.length > 1) {
    return segments.slice(1)
  }
  return segments
}
