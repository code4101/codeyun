export type FeaturePermissionNodeType = 'group' | 'feature'

export type FeatureDirectoryMenuIcon =
  | 'home'
  | 'tools'
  | 'ai'
  | 'attendance'
  | 'game'
  | 'notes'
  | 'cluster'
  | 'admin'
  | 'contact'

export interface FeatureDirectoryMenuItemDefinition {
  path: string
  title?: string
}

export interface FeaturePermissionNodeDefinition {
  key: string
  title: string
  node_type: FeaturePermissionNodeType
  parent_key?: string
  sort_order: number
  route_paths: string[]
  menu_paths: string[]
  menu_items?: FeatureDirectoryMenuItemDefinition[]
  menu_icon?: FeatureDirectoryMenuIcon
  menu_slot?: 'main' | 'footer'
  menu_items_inline?: boolean
  api_scopes: string[]
  default_anonymous_allow: boolean
}

export interface FeaturePermissionRegistryDefinition {
  version: number
  nodes: FeaturePermissionNodeDefinition[]
}

export interface FeaturePermissionTreeNode extends FeaturePermissionNodeDefinition {
  children: FeaturePermissionTreeNode[]
}
