import type { LocationQueryRaw, Router } from 'vue-router'

/** The library mounts its closed reader before a book has been selected. */
export function bookReaderHref(router: Router, bookId: string, query: LocationQueryRaw = {}): string {
  if (!bookId) return ''
  return router.resolve({ name: 'BookResource', params: { bookId }, query }).href
}
