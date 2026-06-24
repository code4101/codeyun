import api from './index'

export type MystiaCatalogKind =
  | 'foods'
  | 'ingredients'
  | 'beverages'
  | 'recipes'
  | 'guests'
  | 'special_guests'
  | 'images'
  | 'audio'

export interface MystiaCatalogSummary {
  schema_version: number
  source: Record<string, unknown>
  stats: Record<string, number>
}

export interface MystiaCatalogListResponse<T = Record<string, unknown>> {
  kind: MystiaCatalogKind
  query: string
  total: number
  items: T[]
  stats: Record<string, number>
  source: Record<string, unknown>
}

export const getMystiaCatalogSummary = async (): Promise<MystiaCatalogSummary> => {
  const response = await api.get('/mystia/catalog')
  return response.data
}

export const getMystiaCatalogEntries = async <T = Record<string, unknown>>(
  kind: MystiaCatalogKind,
  query = '',
): Promise<MystiaCatalogListResponse<T>> => {
  const response = await api.get(`/mystia/catalog/${kind}`, {
    params: query ? { q: query } : undefined,
  })
  return response.data
}
