import type { RouteRecordRaw } from 'vue-router'
import type { UiPresentationOptions } from './uiPresentation'

export interface AppPageDefinition extends UiPresentationOptions {
  routeName: string
  canonicalPath: string
  component: NonNullable<RouteRecordRaw['component']>
  permissionKey?: string
  requiresAuth?: boolean
  requiresAdmin?: boolean
  ipadOnly?: boolean
  menuPath?: string | null
}
