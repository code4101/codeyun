import type { AppPageDefinition } from '@/router/pageRegistryTypes'
import type { FeaturePermissionNodeDefinition } from '@/features/access/permissionRegistryTypes'

export interface PluginMenuItem {
  key: string
  title: string
  path: string
  requiresAuth?: boolean
  requiresAdmin?: boolean
}

export interface PluginMenuSection {
  key: string
  title: string
  permissionKey?: string
  items: PluginMenuItem[]
}

export interface PluginFrontendModule {
  pages?: AppPageDefinition[]
  menuSections?: PluginMenuSection[]
}

type PluginPermissionRegistryModule =
  | FeaturePermissionNodeDefinition[]
  | { nodes?: FeaturePermissionNodeDefinition[] }

const pluginModuleFiles = import.meta.glob<{ default?: PluginFrontendModule }>(
  './modules/*/index.ts',
  { eager: true },
)
const pluginPermissionFiles = import.meta.glob<{ default: PluginPermissionRegistryModule }>(
  './modules/*/permissionRegistry.json',
  { eager: true },
)

const pluginModules = Object.values(pluginModuleFiles)
  .map((file) => file.default)
  .filter((module): module is PluginFrontendModule => Boolean(module))

function normalizePluginPage(page: AppPageDefinition): AppPageDefinition {
  return {
    ...page,
    requiresAuth: page.requiresAuth ?? true,
    requiresAdmin: page.requiresAdmin ?? false,
    menuPath: page.menuPath ?? page.canonicalPath,
  }
}

function normalizePluginMenuSection(section: PluginMenuSection): PluginMenuSection {
  return {
    ...section,
    items: section.items.map((item) => ({
      ...item,
      requiresAuth: item.requiresAuth ?? true,
      requiresAdmin: item.requiresAdmin ?? false,
    })),
  }
}

function normalizePluginPermissionModule(
  moduleValue: PluginPermissionRegistryModule,
): FeaturePermissionNodeDefinition[] {
  if (Array.isArray(moduleValue)) {
    return moduleValue
  }
  return moduleValue.nodes ?? []
}

export const pluginPageRegistry: AppPageDefinition[] = pluginModules.flatMap((module) =>
  (module.pages ?? []).map((page) => normalizePluginPage(page)),
)

export const pluginPermissionNodes: FeaturePermissionNodeDefinition[] = Object.values(
  pluginPermissionFiles,
).flatMap((module) => normalizePluginPermissionModule(module.default))

export const pluginMenuSections: PluginMenuSection[] = pluginModules.flatMap((module) =>
  (module.menuSections ?? []).map((section) => normalizePluginMenuSection(section)),
)

function pathMatches(menuPath: string, currentPath: string): boolean {
  return currentPath === menuPath || currentPath.startsWith(`${menuPath}/`)
}

export function findPluginMenuIndex(currentPath: string): string | null {
  for (const section of pluginMenuSections) {
    const matchedItem = section.items.find((item) => pathMatches(item.path, currentPath))
    if (matchedItem) {
      return matchedItem.path
    }
  }
  return null
}

export function getDefaultPluginOpeneds(currentPath: string): string[] {
  return pluginMenuSections
    .filter((section) => section.items.some((item) => pathMatches(item.path, currentPath)))
    .map((section) => section.key)
}

export function isPluginMenuItemVisible(
  item: PluginMenuItem,
  isAuthenticated: boolean,
  isAdmin: boolean,
): boolean {
  if (item.requiresAdmin) {
    return isAdmin
  }
  if (item.requiresAuth) {
    return isAuthenticated
  }
  return true
}
