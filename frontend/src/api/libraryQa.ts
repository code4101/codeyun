import api from './index'

export interface QaMessage { role: 'user' | 'assistant'; content: string }
export interface QaSource { number: number; title: string; chapter_id: string; page: number | null }
export interface QaAnswer {
  answer: string
  model: string
  sources: QaSource[]
  context: { location: string; warnings: string[]; characters: number }
}
export interface ReadingQuestion {
  resource_type: 'rich-text' | 'pdf' | 'skill-book'
  resource_id: string
  chapter_id: string
  page: number
  bookshelf_id: string
  question: string
  history: QaMessage[]
}
export async function askReadingQuestion(payload: ReadingQuestion, signal: AbortSignal) {
  const response = await api.post<QaAnswer>('/library-qa/ask', payload, { timeout: 120_000, signal })
  return response.data
}
