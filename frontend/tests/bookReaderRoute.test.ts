import assert from 'node:assert/strict'
import test from 'node:test'
import { createMemoryHistory, createRouter } from 'vue-router'
import { bookReaderHref, readerLocation, readerTarget } from '../src/standard/pdf/library/bookReaderRoute.ts'

test('reader links contain only a public number and requested reading options', () => {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/reader', name: 'ReaderWorkspace', component: {} }] })
  assert.equal(bookReaderHref(router), '')
  assert.equal(bookReaderHref(router, 60000), '/reader?id=60000')
  const tab = { kind: 'ebook' as const, id: 'ebook:2:hash', publicId: 60000 }
  assert.equal(router.resolve(readerLocation(tab)).href, '/reader?id=60000')
  assert.equal(router.resolve(readerLocation({ kind: 'pdf', id: '58429' })).href, '/reader?id=58429')
  assert.equal(router.resolve(readerLocation()).href, '/reader')
  assert.equal(router.resolve(readerLocation({ ...tab, readingMode: 'paginated', bookshelfId: 'shelf' })).query.mode, 'paginated')
})

test('route accepts positive safe numeric identifiers only', () => {
  assert.equal(readerTarget({ id: '58429' }), 58429)
  for (const id of ['', '0', '-1', 'ebook:2:hash', '1.5', '9007199254740992', ['1', '2']]) {
    assert.equal(readerTarget({ id }), undefined)
  }
})
