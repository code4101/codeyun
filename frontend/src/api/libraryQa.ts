import api from './index'

export interface QaMessage { role: 'user' | 'assistant'; content: string }
export interface QaTurn extends QaMessage { location: string; result?: QaAnswer }
export interface QaScope { resource_type: ReadingQuestion['resource_type']; resource_id: string }
export interface QaConversation extends QaScope { turns: QaTurn[]; revision: number; updated_at?: number | null }
export async function fetchQaConversation(scope: QaScope) {
  return (await api.get<QaConversation>('/library-qa/conversation', { params: scope })).data
}
export async function saveQaConversation(payload: QaConversation) {
  return (await api.put<QaConversation>('/library-qa/conversation', payload)).data
}
export interface QaSource { number: number; title: string; chapter_id: string; page: number | null }
export interface QaAnswer {
  answer: string
  model: string
  sources: QaSource[]
  web_sources?: { title: string; url: string }[]
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
