import api from '@/api'

export type FeatureAccessDecision = 'inherit' | 'allow' | 'deny'
export type FeatureAccessNodeType = 'group' | 'feature'
export type FeatureAccessSource =
  | 'default_allow'
  | 'default_deny'
  | 'explicit_allow'
  | 'explicit_deny'
  | 'inherit_anonymous'
  | 'ancestor_denied'
  | 'superuser'

export interface FeatureAccessFlatItem {
  key: string
  title: string
  node_type: FeatureAccessNodeType
  parent_key: string | null
  sort_order: number
  route_paths: string[]
  menu_paths: string[]
  api_scopes: string[]
  default_anonymous_allow: boolean
  local_decision: FeatureAccessDecision
  base_value: boolean
  effective_value: boolean
  inherited_effective_value: boolean | null
  disabled_by_ancestor: boolean
  source: FeatureAccessSource
}

export interface FeatureAccessTreeItem extends FeatureAccessFlatItem {
  children: FeatureAccessTreeItem[]
}

export interface FeatureAccessContextSubject {
  kind: 'anonymous' | 'user'
  is_authenticated: boolean
  is_superuser: boolean
  user_id: number | null
  username: string | null
}

export interface FeatureAccessContext {
  registry_version: number
  subject: FeatureAccessContextSubject
  overrides: Record<string, FeatureAccessDecision>
  items: FeatureAccessTreeItem[]
  flat_items: Record<string, FeatureAccessFlatItem>
  effective_keys: string[]
}

export const fetchAccessContext = async (): Promise<FeatureAccessContext> => {
  const response = await api.get('/access/context')
  return response.data
}
