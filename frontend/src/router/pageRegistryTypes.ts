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
