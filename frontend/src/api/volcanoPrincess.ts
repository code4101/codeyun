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

export interface VolcanoPrincessTheaterQuestion {
  index: number
  line_type_index: number
  line_type: string
  line_index: number
  content: string
}

export interface VolcanoPrincessTheaterRequirement {
  nature_index: number
  nature: string
  value: number
}

export interface VolcanoPrincessTheaterDrama {
  index: number
  name: string
  description: string
  role: string
  theater_level: number
  category_index: number
  category: string
  drama_variant: number
  sponsor_index: number
  requirements: VolcanoPrincessTheaterRequirement[]
  charm: number
  base_salary: number
  fame: number
}

export interface VolcanoPrincessTheaterCatalog {
  generated_at: string
  source: {
    build_id?: string
    engine?: string
    data_sha256?: string
    txt_sha256?: string
    assembly_sha256?: string
  }
  summary: {
    drama_count: number
    question_count: number
    line_type_count: number
    drama_category_count: number
  }
  mechanics: {
    rounds: number
    options_per_round: number
    energy_cost: number
    shared_question_bank: boolean
    correct_rule: string
    correct_answer_bonus: number
    performance_bgm_index: number
    performance_bgm_name: string
    performance_bgm_path_id: number
  }
  line_types: Array<{ index: number; name: string; game_color: string }>
  drama_categories: string[]
  nature_names: string[]
  questions: VolcanoPrincessTheaterQuestion[]
  dramas: VolcanoPrincessTheaterDrama[]
}

export async function getVolcanoPrincessTheaterCatalog(): Promise<VolcanoPrincessTheaterCatalog> {
  const response = await api.get('/volcano-princess/theater')
  return response.data
}
