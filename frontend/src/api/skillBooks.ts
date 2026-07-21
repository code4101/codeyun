import api from '@/api'

export interface SkillBookChapter {
  id: string
  skill_id: string
  title: string
  relative_path: string
  kind: 'main' | 'reference'
  revision: string
  character_count: number
  updated_at: number
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
  title: string
  author: string
  cover_color: string
  revision: string
  skill_count: number
  chapter_count: number
  estimated_page_count: number
  updated_at: number
  skills: SkillBookSkill[]
}

export interface SkillBookChapterContent {
  book_id: string
  chapter: SkillBookChapter
  markdown: string
}

const noCacheConfig = () => ({
  params: { _t: Date.now() },
  headers: { 'Cache-Control': 'no-cache' },
})

export async function fetchLocalSkillBookCatalog() {
  const response = await api.get<SkillBookCatalog>('/skill-books/local/catalog', noCacheConfig())
  return response.data
}

export async function fetchLocalSkillBookChapter(chapterId: string) {
  const response = await api.get<SkillBookChapterContent>(
    `/skill-books/local/chapters/${encodeURIComponent(chapterId)}`,
    noCacheConfig(),
  )
  return response.data
}
