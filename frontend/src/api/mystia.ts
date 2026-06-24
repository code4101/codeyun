import api from './index'

export type MystiaCatalogKind =
  | 'foods'
  | 'ingredients'
  | 'beverages'
  | 'guests'
  | 'special_guests'
  | 'special_guest_records'
  | 'locations'
  | 'images'
  | 'audio'


export interface MystiaCatalogListResponse<T = Record<string, unknown>> {
  kind: MystiaCatalogKind
  query: string
  page: number
  page_size: number
  sort_by: string
  sort_order: string
  total: number
  total_pages: number
  items: T[]
  stats: Record<string, number>
  source: Record<string, unknown>
}

export interface MystiaCatalogListQuery {
  query?: string
  page?: number
  pageSize?: number
  sortBy?: string
  sortOrder?: 'asc' | 'desc' | ''
}

export const getMystiaCatalogEntries = async <T = Record<string, unknown>>(
  kind: MystiaCatalogKind,
  query: MystiaCatalogListQuery = {},
): Promise<MystiaCatalogListResponse<T>> => {
  const params: Record<string, string | number> = {}
  if (query.query) params.q = query.query
  if (query.page) params.page = query.page
  if (query.pageSize) params.page_size = query.pageSize
  if (query.sortBy) params.sort_by = query.sortBy
  if (query.sortOrder) params.sort_order = query.sortOrder
  const response = await api.get(`/mystia/catalog/${kind}`, {
    params,
  })
  return response.data
}

