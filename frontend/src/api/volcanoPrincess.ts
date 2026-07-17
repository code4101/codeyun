import api from './index'

export type VolcanoPrincessAudioCategory = 'music_or_ambience' | 'voice_or_effect' | 'short_clip'
export type VolcanoPrincessAudioSort = 'path_id' | 'name' | 'duration'

export interface VolcanoPrincessAudioEntry {
  id: string
  path_id: number
  name: string
  category: VolcanoPrincessAudioCategory
  duration_seconds: number
  channels: number
  frequency_hz: number
  source_asset: string
  media_bytes: number
  media_sha256: string
  media_url: string
}

export interface VolcanoPrincessAudioMeta {
  app_id: string
  app_name: string
  generated_at: string
  source: {
    platform?: string
    steam_app_id?: string
    build_id?: string
    engine?: string
    architecture?: string
    scripting_backend?: string
  }
  summary: {
    entry_count?: number
    duration_seconds?: number
    exported_count?: number
    failed_count?: number
    media_bytes?: number
    category_counts?: Record<string, number>
  }
  categories: VolcanoPrincessAudioCategory[]
}

export interface VolcanoPrincessAudioListResponse {
  items: VolcanoPrincessAudioEntry[]
  page: number
  page_size: number
  total: number
  total_pages: number
  source: {
    build_id?: string
    engine?: string
  }
}

export interface VolcanoPrincessAudioListQuery {
  q?: string
  category?: VolcanoPrincessAudioCategory | ''
  sortBy?: VolcanoPrincessAudioSort
  sortOrder?: 'asc' | 'desc'
  page?: number
  pageSize?: number
}

export async function getVolcanoPrincessAudioMeta(): Promise<VolcanoPrincessAudioMeta> {
  const response = await api.get('/volcano-princess/audio/meta')
  return response.data
}

export async function getVolcanoPrincessAudioEntries(
  query: VolcanoPrincessAudioListQuery,
): Promise<VolcanoPrincessAudioListResponse> {
  const response = await api.get('/volcano-princess/audio', {
    params: {
      q: query.q || undefined,
      category: query.category || undefined,
      sort_by: query.sortBy,
      sort_order: query.sortOrder,
      page: query.page,
      page_size: query.pageSize,
    },
  })
  return response.data
}

export async function getVolcanoPrincessAudioEntry(pathId: number): Promise<VolcanoPrincessAudioEntry> {
  const response = await api.get(`/volcano-princess/audio/${pathId}`)
  return response.data
}

