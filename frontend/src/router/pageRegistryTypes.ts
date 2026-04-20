import type { RouteRecordRaw } from 'vue-router'

export interface AppPageDefinition {
  routeName: string
  canonicalPath: string
  component: RouteRecordRaw['component']
  permissionKey?: string
  requiresAuth?: boolean
  requiresAdmin?: boolean
  standaloneEnabled?: boolean
  menuPath?: string | null
}

export interface PrivatePageDefinition extends AppPageDefinition {
  menuSectionKey: string
  menuSectionTitle: string
  menuItemKey: string
  menuItemTitle: string
}
