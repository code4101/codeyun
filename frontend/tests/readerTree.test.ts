import assert from 'node:assert/strict'
import test from 'node:test'
import { activeSectionId, appendChapterSections, appendReaderHeadings, chapterSectionHeadings, chapterOutlineHeadings, firstTocArticle, withTocGroups, splitReaderTree, readerTreeAncestors, readerTreeRows } from '../src/standard/pdf/library/readerTree.ts'

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

test('chapter outline drops only the heading that repeats the chapter title', () => {
  const chapterOnly = [{ id: 'h', title: '01.  第一集', level: 2 }]
  assert.deepEqual(chapterOutlineHeadings(chapterOnly, '01. 第一集'), [])
  const nested = [{ id: 'h', title: '01. 第一集', level: 2 }, { id: 's', title: '小节', level: 3 }]
  assert.deepEqual(chapterOutlineHeadings(nested, '01. 第一集').map(x => x.id), ['s'])
  assert.deepEqual(chapterOutlineHeadings([{ id: 'x', title: '引言', level: 2 }], '01. 第一集').map(x => x.id), ['x'])
})

test('catalog-only groups precede their first chapter and open it when selected', () => {
  const source = [
    { anchor: 'part-1' }, { anchor: 'article-1', parent_anchor: 'part-1' }, { anchor: 'article-2', parent_anchor: 'part-1' },
    { anchor: 'part-2' }, { anchor: 'article-3', parent_anchor: 'part-2' },
  ]
  const articles = source.filter(item => item.anchor.startsWith('article-'))
  assert.deepEqual(withTocGroups(articles, source).map(x => x.anchor), source.map(x => x.anchor))
  assert.equal(firstTocArticle('part-2', articles, source), 'article-3')
  assert.equal(firstTocArticle('article-2', articles, source), 'article-2')
  const flat = [{ anchor: 'article-1' }, { anchor: 'article-2' }]
  assert.equal(withTocGroups(flat, flat), flat)
})

test('layered catalog lists each chapter top section level and tracks the section being read', () => {
  const headings = [
    { id: 'article-1', title: '01. 甲', level: 3 },
    { id: 'h1', title: '一节', level: 4 }, { id: 'h2', title: '小节', level: 5 }, { id: 'h3', title: '二节', level: 4 },
  ]
  const sections = chapterSectionHeadings(headings, '01. 甲')
  assert.deepEqual(sections.map(x => x.id), ['h1', 'h3'])
  assert.deepEqual(chapterSectionHeadings([{ id: 'article-2', title: '02. 乙', level: 3 }], '02. 乙'), [])
  const tree = appendChapterSections([{ id: 'part-1', title: '部' }, { id: 'article-1', title: '01. 甲', parentId: 'part-1' }], { 'article-1': sections })
  assert.deepEqual(tree.map(x => [x.id, x.parentId ?? null]), [['part-1', null], ['article-1', 'part-1'], ['heading:h1', 'article-1'], ['heading:h3', 'article-1']])
  const ids = new Set(sections.map(x => x.id))
  assert.equal(activeSectionId(headings, ids, 'h2'), 'h1')
  assert.equal(activeSectionId(headings, ids, 'h3'), 'h3')
  assert.equal(activeSectionId(headings, ids, 'article-1'), '')
})
