import type { RouteRecordRaw } from 'vue-router'
import type { UiPresentationOptions } from './uiPresentation'

export interface AppPageDefinition extends UiPresentationOptions {
  /** workspace 自己分配 Pane；document 使用 Shell 内的单个文档 Pane。 */
  pageLayout?: 'workspace' | 'document'
  routeName: string
  canonicalPath: string
  component: NonNullable<RouteRecordRaw['component']>
  permissionKey?: string
  requiresAuth?: boolean
  requiresAdmin?: boolean
  ipadOnly?: boolean
  menuPath?: string | null
}
