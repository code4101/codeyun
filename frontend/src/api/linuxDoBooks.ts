import api from './index'

export interface LinuxDoBookTocItem {
  title: string
  number: string
  level: number
  anchor: string
  parent_anchor: string | null
  source_post_number: number | null
  inferred: boolean
}

export interface LinuxDoBookPlacement {
  bookshelf_id: string
  shelf_index: number
  position_index: number
  orientation: 'spine_vertical' | 'spine_horizontal' | 'cover_front'
  folder_id: string | null
  article_reading_mode?: 'scroll' | 'paginated' | null
}

export interface LinuxDoBookSummary {
  id: string
  topic_id: number
  title: string
  author: string
  start_date: string
  source_url: string
  book_kind: string
  format: string
  original_filename: string
  cover_color: string
  revision: string
  toc_count: number
  post_count: number
  selected_reply_count: number
  estimated_page_count: number
  imported_at: number
  updated_at: number
  latest_issue: number | null
  capabilities: {
    can_annotate: boolean
    can_edit_content: boolean
    edit_mode: 'html' | 'source' | null
    source_policy: 'owned' | 'derived' | 'external'
  }
  bookshelf_placement: LinuxDoBookPlacement
  reading_state: LinuxDoBookReadingState | null
}

export interface LinuxDoBookContent extends LinuxDoBookSummary {
  content_html: string
  content_markdown: string
  toc: LinuxDoBookTocItem[]
}

export interface LinuxDoBookReadingState {
  book_id: string
  chapter_id: string
  character_offset: number
  chapter_revision: string
  current_page: number
  page_count: number
  updated_at: number | null
}

export interface EditableEbookSource {
  content: string
  revision: string
  format: 'html' | 'markdown' | 'text'
  filename: string
}

export async function importLinuxDoBook(url: string, bookshelfId?: string) {
  const response = await api.post<LinuxDoBookSummary>('/linux-do-books/import', {
    url,
    bookshelf_id: bookshelfId || null,
  }, { timeout: 120_000 })
  return response.data
}

export async function uploadElectronicBook(file: File, bookshelfId: string, shelfIndex = 0) {
  const payload = new FormData()
  payload.append('file', file, file.name)
  payload.append('bookshelf_id', bookshelfId)
  payload.append('shelf_index', String(shelfIndex))
  const response = await api.post<LinuxDoBookSummary>('/linux-do-books/upload', payload, {
    timeout: 10 * 60 * 1000,
  })
  return response.data
}

export async function fetchElectronicBookResource(resourceUrl: string) {
  const apiPath = resourceUrl.startsWith('/api/') ? resourceUrl.slice(4) : resourceUrl
  const response = await api.get<Blob>(apiPath, { responseType: 'blob' })
  return response.data
}

export async function fetchLinuxDoBooks(bookshelfId?: string) {
  const response = await api.get<LinuxDoBookSummary[]>('/linux-do-books', {
    params: bookshelfId ? { bookshelf_id: bookshelfId } : undefined,
  })
  return response.data
}

export async function fetchLinuxDoBook(bookId: string) {
  const response = await api.get<LinuxDoBookContent>(`/linux-do-books/${encodeURIComponent(bookId)}`)
  return response.data
}

export async function updateHtmlBookArticle(
  bookId: string,
  articleId: string,
  payload: { content_html: string; revision: string },
) {
  const response = await api.put<LinuxDoBookContent>(
    `/linux-do-books/${encodeURIComponent(bookId)}/articles/${encodeURIComponent(articleId)}`,
    payload,
  )
  return response.data
}

export async function fetchEditableEbookSource(bookId: string) {
  const response = await api.get<EditableEbookSource>(
    `/linux-do-books/${encodeURIComponent(bookId)}/source`,
  )
  return response.data
}

export async function updateEditableEbookSource(
  bookId: string,
  payload: Pick<EditableEbookSource, 'content' | 'revision'>,
) {
  const response = await api.put<LinuxDoBookContent>(
    `/linux-do-books/${encodeURIComponent(bookId)}/source`,
    payload,
  )
  return response.data
}

export async function updateLinuxDoBookPlacement(bookId: string, payload: LinuxDoBookPlacement) {
  const response = await api.put<LinuxDoBookPlacement>(
    `/linux-do-books/${encodeURIComponent(bookId)}/placement`,
    payload,
  )
  return response.data
}

export async function updateLinuxDoBookMetadata(
  bookId: string,
  payload: Pick<LinuxDoBookSummary, 'title' | 'author' | 'start_date' | 'cover_color'>,
) {
  const response = await api.put<LinuxDoBookSummary>(
    `/linux-do-books/${encodeURIComponent(bookId)}/metadata`,
    payload,
  )
  return response.data
}

export async function deleteLinuxDoBook(bookId: string) {
  await api.delete(`/linux-do-books/${encodeURIComponent(bookId)}`)
}

export async function fetchLinuxDoBookReadingState(bookId: string) {
  const response = await api.get<LinuxDoBookReadingState>(
    `/linux-do-books/${encodeURIComponent(bookId)}/reading-state`,
  )
  return response.data
}

export async function updateLinuxDoBookReadingState(
  bookId: string,
  payload: Pick<
    LinuxDoBookReadingState,
    'chapter_id' | 'character_offset' | 'chapter_revision' | 'current_page' | 'page_count'
  >,
) {
  const response = await api.put<LinuxDoBookReadingState>(
    `/linux-do-books/${encodeURIComponent(bookId)}/reading-state`,
    payload,
  )
  return response.data
}
