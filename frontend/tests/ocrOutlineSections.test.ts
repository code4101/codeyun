import assert from 'node:assert/strict'
import test from 'node:test'
import { ocrOutlineSections } from '../src/standard/pdf/resource-view/ocrOutlineSections.ts'
import { trimReadingPage } from '../src/standard/pdf/resource-view/ocrSectionBoundary.ts'

const entries = [
  { id: 'parent', title: '6.4', page: 560, level: 1 },
  { id: 'a', title: '6.4.1', page: 560, level: 2 },
  { id: 'b', title: '6.4.2', page: 561, level: 2 },
  { id: 'c', title: '6.4.3', page: 565, level: 2 },
  { id: 'next', title: '6.5', page: 577, level: 1 },
]
test('OCR aggregates the outline branch, preserving same-page title boundaries', () => {
  const sections = ocrOutlineSections(entries, ['a', 'b', 'c'], 'parent', 995, 560)
  assert.deepEqual(sections.map(s => s.id), ['parent', 'a', 'b', 'c'])
  assert.deepEqual(sections.map(s => [s.start, s.end, s.nextTitle]), [[560, 560, '6.4.1'], [560, 561, '6.4.2'], [561, 565, '6.4.3'], [565, 577, '6.5']])
  assert.deepEqual(ocrOutlineSections(entries, ['a', 'b', 'c'], 'parent', 995, 565), sections)
})
test('books without bookmarks retain a readable body range', () => {
  assert.deepEqual(ocrOutlineSections([], [], '', 10, 3).map(s => [s.start, s.end, s.hasTitle]), [[1, 10, false]])
})

test('shared boundary pages contribute each paragraph to only one section', () => {
  const page = { page: 560, available: true, blocks: [
    {kind: 'heading', text: '6.4'}, {kind: 'paragraph', text: '章节引言'},
    {kind: 'heading', text: '6.4.1'}, {kind: 'paragraph', text: '第一节正文'},
    {kind: 'heading', text: '6.4.2'}, {kind: 'paragraph', text: '第二节正文'},
  ] } as Parameters<typeof trimReadingPage>[0]
  assert.deepEqual(trimReadingPage(page, '6.4', '6.4.1').blocks.map(b => b.text), ['章节引言'])
  assert.deepEqual(trimReadingPage(page, '6.4.1', '6.4.2').blocks.map(b => b.text), ['第一节正文'])
  assert.deepEqual(trimReadingPage(page, '6.4.2').blocks.map(b => b.text), ['第二节正文'])
})
