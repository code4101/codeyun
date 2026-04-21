export type FeaturePermissionNodeType = 'group' | 'feature'

export interface FeaturePermissionNodeDefinition {
  key: string
  title: string
  node_type: FeaturePermissionNodeType
  parent_key?: string
  sort_order: number
  route_paths: string[]
  menu_paths: string[]
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
