import { fetchPdfPagePreview } from '@/api/pdfDocuments'

const PREVIEW_BLOCK_SIZE = 5
const PREVIEW_CACHE_TTL_MS = 15 * 60 * 1000
const PREVIEW_CACHE_MAX_PAGES = 60

interface PreviewPageCacheEntry {
  url: string
  expiresAt: number
  lastAccessedAt: number
}

const previewPageCache = new Map<string, PreviewPageCacheEntry>()
const previewPageRequests = new Map<string, Promise<string>>()

function previewPageKey(pdfId: number, pageNumber: number) {
  return `${pdfId}:${pageNumber}`
}

function removePreviewPage(key: string, entry: PreviewPageCacheEntry) {
  previewPageCache.delete(key)
  URL.revokeObjectURL(entry.url)
}

function prunePreviewPageCache(now = Date.now()) {
  for (const [key, entry] of previewPageCache) {
    if (entry.expiresAt <= now) {
      removePreviewPage(key, entry)
    }
  }

  if (previewPageCache.size <= PREVIEW_CACHE_MAX_PAGES) {
    return
  }

  const oldestEntries = [...previewPageCache.entries()]
    .sort(([, left], [, right]) => left.lastAccessedAt - right.lastAccessedAt)
  for (const [key, entry] of oldestEntries.slice(0, previewPageCache.size - PREVIEW_CACHE_MAX_PAGES)) {
    removePreviewPage(key, entry)
  }
}

export function getCachedPreviewPageUrl(pdfId: number, pageNumber: number) {
  const key = previewPageKey(pdfId, pageNumber)
  const entry = previewPageCache.get(key)
  const now = Date.now()
  if (!entry || entry.expiresAt <= now) {
    if (entry) {
      removePreviewPage(key, entry)
    }
    return ''
  }
  entry.lastAccessedAt = now
  return entry.url
}

async function ensurePreviewPage(pdfId: number, pageNumber: number) {
  const cachedUrl = getCachedPreviewPageUrl(pdfId, pageNumber)
  if (cachedUrl) {
    return cachedUrl
  }

  const key = previewPageKey(pdfId, pageNumber)
  const pendingRequest = previewPageRequests.get(key)
  if (pendingRequest) {
    return pendingRequest
  }

  const request = fetchPdfPagePreview(pdfId, pageNumber)
    .then((blob) => {
      const now = Date.now()
      const url = URL.createObjectURL(blob)
      previewPageCache.set(key, {
        url,
        expiresAt: now + PREVIEW_CACHE_TTL_MS,
        lastAccessedAt: now,
      })
      prunePreviewPageCache(now)
      return url
    })
    .finally(() => {
      previewPageRequests.delete(key)
    })
  previewPageRequests.set(key, request)
  return request
}

function previewBlockPages(pageNumber: number, pageCount: number) {
  const blockStart = Math.floor((pageNumber - 1) / PREVIEW_BLOCK_SIZE) * PREVIEW_BLOCK_SIZE + 1
  const blockEnd = Math.min(pageCount, blockStart + PREVIEW_BLOCK_SIZE - 1)
  return Array.from({ length: blockEnd - blockStart + 1 }, (_, index) => blockStart + index)
}

export async function loadPreviewPageBlock(pdfId: number, pageNumber: number, pageCount: number) {
  // 目标页先进入请求队列，其余同组页面在后台补齐，避免预加载拖慢当前页。
  const targetRequest = ensurePreviewPage(pdfId, pageNumber)
  const neighborPages = previewBlockPages(pageNumber, pageCount).filter((page) => page !== pageNumber)
  void Promise.allSettled(neighborPages.map((page) => ensurePreviewPage(pdfId, page)))
  return targetRequest
}
