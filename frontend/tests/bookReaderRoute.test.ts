import assert from 'node:assert/strict'
import test from 'node:test'
import { computed, ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { bookReaderHref } from '../src/standard/pdf/library/bookReaderRoute.ts'

test('closed library reader can render before selection, then link to a selected book', () => {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/book/:bookId', name: 'BookResource', component: {} }],
  })
  const selected = ref('')
  const href = computed(() => bookReaderHref(router, selected.value, { mode: 'paginated', pageSize: '1600' }))
  assert.equal(href.value, '')
  selected.value = 'ebook:2:abc'
  const resolved = router.resolve(href.value)
  assert.equal(resolved.params.bookId, selected.value)
  assert.equal(resolved.query.mode, 'paginated')
  assert.equal(resolved.query.pageSize, '1600')
  selected.value = ''
  assert.equal(href.value, '')
  const skill = router.resolve(bookReaderHref(router, 'local-skill', { bookshelf: 'shelf-1' }))
  assert.equal(skill.params.bookId, 'local-skill')
  assert.equal(skill.query.bookshelf, 'shelf-1')
})
