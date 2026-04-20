import type { PrivatePageDefinition } from '@/router/pageRegistryTypes'

export interface PrivateMenuItem {
  key: string
  title: string
  path: string
  requiresAuth?: boolean
  requiresAdmin?: boolean
}

export interface PrivateMenuSection {
  key: string
  title: string
  items: PrivateMenuItem[]
}

export interface PrivateFrontendModule {
  pages?: PrivatePageDefinition[]
}

const privateModuleFiles = import.meta.glob<{ default?: PrivateFrontendModule }>(
  './modules/*/index.ts',
  { eager: true },
)

const privateModules = Object.values(privateModuleFiles)
  .map((file) => file.default)
  .filter((module): module is PrivateFrontendModule => Boolean(module))

function normalizePrivatePage(page: PrivatePageDefinition): PrivatePageDefinition {
  return {
    ...page,
    requiresAuth: page.requiresAuth ?? true,
    requiresAdmin: page.requiresAdmin ?? false,
    menuPath: page.menuPath ?? page.canonicalPath,
  }
}

export const privatePageRegistry = privateModules.flatMap((module) =>
  (module.pages ?? []).map((page) => normalizePrivatePage(page)),
)

const privateMenuSectionMap = privatePageRegistry.reduce((sections, page) => {
  const section = sections.get(page.menuSectionKey) ?? {
    key: page.menuSectionKey,
    title: page.menuSectionTitle,
    items: [] as PrivateMenuItem[],
  }
  section.items.push({
    key: page.menuItemKey,
    title: page.menuItemTitle,
    path: page.menuPath ?? page.canonicalPath,
    requiresAuth: page.requiresAuth,
    requiresAdmin: page.requiresAdmin,
  })
  sections.set(page.menuSectionKey, section)
  return sections
}, new Map<string, PrivateMenuSection>())

export const privateMenuSections = Array.from(privateMenuSectionMap.values())

function pathMatches(menuPath: string, currentPath: string): boolean {
  return currentPath === menuPath || currentPath.startsWith(`${menuPath}/`)
}

export function findPrivateMenuIndex(currentPath: string): string | null {
  for (const section of privateMenuSections) {
    const matchedItem = section.items.find((item) => pathMatches(item.path, currentPath))
    if (matchedItem) {
      return matchedItem.path
    }
  }
  return null
}

export function getDefaultPrivateOpeneds(currentPath: string): string[] {
  return privateMenuSections
    .filter((section) => section.items.some((item) => pathMatches(item.path, currentPath)))
    .map((section) => section.key)
}

export function isPrivateMenuItemVisible(
  item: PrivateMenuItem,
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
