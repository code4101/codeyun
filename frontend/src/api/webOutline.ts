import api from './index'

export interface WebOutlineSourceHeading {
  source_index: number
  title: string
  html_level: number
  context: string
}

export interface WebOutlineItem {
  title: string
  level: number
  number: string
  source_index: number | null
  inferred: boolean
}

export interface WebOutlineResult {
  url: string
  title: string
  source_headings: WebOutlineSourceHeading[]
  items: WebOutlineItem[]
  markdown: string
}

export async function extractWebOutline(url: string) {
  const response = await api.post<WebOutlineResult>(
    '/web-outline/extract',
    { url },
    { timeout: 30_000 },
  )
  return response.data
}
