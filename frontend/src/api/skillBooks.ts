import api from '@/api'

export interface SkillBookChapter {
  id: string
  skill_id: string
  title: string
  relative_path: string
  kind: 'main' | 'reference'
  revision: string
  character_count: number
  book_character_start: number
  reading_unit_count: number
  estimated_page_count: number
  page_start: number
  page_end: number
  created_at: number
  modified_at: number
  updated_at: number
  source_language: 'zh' | 'en' | 'mixed'
}

export interface SkillBookSkill {
  id: string
  name: string
  description: string
  chapters: SkillBookChapter[]
  updated_at: number
}

export interface SkillBookCatalog {
  id: string
  asset_id: string
  title: string
  author: string
  start_date: string
  cover_color: string
  revision: string
  skill_count: number
  chapter_count: number
  estimated_page_count: number
  page_format: string
  page_width_mm: number
  page_height_mm: number
  page_format_options: Array<{
    value: string
    label: string
    width_mm: number
    height_mm: number
  }>
  pagination_version: number
  page_capacity_units: number
  updated_at: number
  skills: SkillBookSkill[]
  owner_user_id: number
  owner_username: string
  is_owned: boolean
  access_role: 'viewer' | 'manager'
  bookshelf_placement: SkillBookPlacement
}

export interface SkillBookPlacement {
  book_id: string
  bookshelf_id: string
  shelf_index: number
  position_index: number
  orientation: 'spine_vertical' | 'spine_horizontal' | 'cover_front'
  folder_id?: string | null
}

export interface SkillBookChapterContent {
  book_id: string
  chapter: SkillBookChapter
  markdown: string
  translation: {
    status: 'not_needed' | 'missing' | 'pending' | 'done' | 'error'
    language: string
    source_revision: string
    revision: string
    markdown: string
    updated_at: number | null
    error_message: string
  }
}

export interface SkillBookTranslationSync {
  eligible_count: number
  ready_count: number
  queued_count: number
  task_id: string | null
  status: 'ready' | 'queued' | 'running'
}

export interface SkillBookReadingState {
  book_id: string
  chapter_id: string
  character_offset: number
  chapter_revision: string
  current_page: number
  pagination_version: number
  page_format: string
  updated_at: number | null
}

const noCacheConfig = () => ({
  params: { _t: Date.now() },
  headers: { 'Cache-Control': 'no-cache' },
})

export async function fetchLocalSkillBookCatalog(bookshelfId: string) {
  const response = await api.get<SkillBookCatalog>('/skill-books/local/catalog', {
    ...noCacheConfig(),
    params: { bookshelf_id: bookshelfId, _t: Date.now() },
  })
  return response.data
}

export async function fetchLocalSkillBookChapter(chapterId: string) {
  const response = await api.get<SkillBookChapterContent>(
    `/skill-books/local/chapters/${encodeURIComponent(chapterId)}`,
    noCacheConfig(),
  )
  return response.data
}

export async function syncLocalSkillBookTranslations() {
  const response = await api.post<SkillBookTranslationSync>('/skill-books/local/translations/sync')
  return response.data
}

export async function fetchLocalSkillBookReadingState() {
  const response = await api.get<SkillBookReadingState>('/skill-books/local/my-state', noCacheConfig())
  return response.data
}

export async function updateLocalSkillBookReadingState(payload: {
  chapter_id: string
  character_offset: number
  chapter_revision: string
}) {
  const response = await api.put<SkillBookReadingState>('/skill-books/local/my-state', payload)
  return response.data
}

export async function updateLocalSkillBookMetadata(payload: {
  page_format: string
  start_date: string
}) {
  const response = await api.put<SkillBookCatalog>('/skill-books/local/metadata', payload)
  return response.data
}

export async function updateLocalSkillBookPlacement(payload: Omit<SkillBookPlacement, 'book_id'>) {
  const response = await api.put<SkillBookPlacement>('/skill-books/local/placement', payload)
  return response.data
}

export async function deleteLocalSkillBook() {
  await api.delete('/skill-books/local')
}
