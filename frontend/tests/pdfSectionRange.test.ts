import assert from 'node:assert/strict'
import test from 'node:test'
import { pdfSectionRange } from '../src/standard/pdf/resource-view/pdfSectionRange.ts'

test('PDF section includes descendants, ends before next peer and clamps shared-page headings', () => {
  const entries = [
    { id: 'chapter', page: 10, level: 0 },
    { id: 'section', page: 12, level: 1 },
    { id: 'child', page: 14, level: 2 },
    { id: 'peer', page: 20, level: 1 },
    { id: 'same', page: 20, level: 1 },
    { id: 'next', page: 30, level: 0 },
  ]
  assert.deepEqual(pdfSectionRange(entries, 'chapter', 40), { start: 10, end: 29 })
  assert.deepEqual(pdfSectionRange(entries, 'section', 40), { start: 12, end: 19 })
  assert.deepEqual(pdfSectionRange(entries, 'peer', 40), { start: 20, end: 20 })
  assert.deepEqual(pdfSectionRange(entries, 'next', 40), { start: 30, end: 40 })
  assert.equal(pdfSectionRange(entries, 'missing', 40), null)
  assert.equal(pdfSectionRange(entries, 'next', 0), null)
})
test('untargeted groups use their first descendant page and the following group boundary', () => {
  assert.deepEqual(pdfSectionRange([
    { id: 'group', page: null, level: 0 },
    { id: 'child', page: 5, level: 1 },
    { id: 'next-group', page: null, level: 0 },
    { id: 'next-child', page: 8, level: 1 },
  ], 'group', 10), { start: 5, end: 7 })
})
