import assert from 'node:assert/strict'
import test from 'node:test'
import { appendReaderHeadings, splitReaderTree, readerTreeAncestors, readerTreeRows } from '../src/standard/pdf/library/readerTree.ts'

test('split level hides deep TOC nodes and scopes the outline to the active branch without page numbers', () => {
  const tree = [
    { id: 'a', title: 'A', number: '1' },
    { id: 'b', title: 'B', parentId: 'a', number: '2' },
    { id: 'c', title: 'C', parentId: 'b', number: '3' },
    { id: 'd', title: 'D', parentId: 'c', number: '4' },
    { id: 'other', title: 'Other' },
    { id: 'hidden', title: 'Other section', parentId: 'other' },
  ]
  const result = splitReaderTree(tree, 'd', 3)
  assert.deepEqual(result.toc.map(x => x.id), ['a', 'b', 'other', 'hidden'])
  assert.equal(result.tocActiveId, 'b')
  assert.deepEqual(result.outline.map(x => x.id), ['c', 'd'])
  assert.equal(result.outline[0]?.parentId, null)
  assert.ok(result.outline.every(x => !('number' in x)))
  assert.deepEqual(splitReaderTree(tree, 'd', 1).toc, [])
  assert.equal(splitReaderTree(tree, 'd', 1).outline.length, tree.length)
  assert.deepEqual(splitReaderTree(tree, 'other', 4).outline, [])
})

test('document headings join their chapter in source order', () => {
  const joined = appendReaderHeadings([{ id: 'a', title: 'A' }, { id: 'b', title: 'B' }], 'a', [
    { id: 'first', title: 'First', level: 2 }, { id: 'sub', title: 'Sub', level: 3 },
  ])
  assert.deepEqual(joined.map(x => x.id), ['a', 'heading:first', 'heading:sub', 'b'])
  assert.equal(joined[2]?.parentId, 'heading:first')
})

const items = [
  { id: 'part', title: '篇' },
  { id: 'chapter', parentId: 'part', title: '章' },
  { id: 'section', parentId: 'chapter', title: '节' },
  { id: 'appendix', title: '附录' },
]

test('nested directory collapse hides the entire branch and preserves siblings', () => {
  assert.deepEqual(readerTreeRows(items, new Set()).map(x => x.depth), [0, 1, 2, 0])
  assert.deepEqual(readerTreeRows(items, new Set(['part'])).map(x => x.id), ['part', 'appendix'])
  assert.deepEqual(readerTreeRows(items, new Set(['chapter'])).map(x => x.id), ['part', 'chapter', 'appendix'])
  assert.deepEqual(readerTreeAncestors(items, 'section'), ['chapter', 'part'])
})

test('missing parents and malformed cycles cannot hang the reader', () => {
  const malformed = [
    { id: 'a', parentId: 'b', title: 'A' },
    { id: 'b', parentId: 'a', title: 'B' },
    { id: 'orphan', parentId: 'missing', title: '孤立章节' },
  ]
  assert.deepEqual(readerTreeAncestors(malformed, 'a'), ['b'])
  assert.equal(readerTreeRows(malformed, new Set()).length, 3)
  assert.equal(readerTreeRows(malformed, new Set())[2]?.depth, 0)
})
