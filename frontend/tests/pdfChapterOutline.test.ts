import assert from 'node:assert/strict'
import test from 'node:test'
import { pdfChapterOutline } from '../src/standard/pdf/resource-view/pdfChapterOutline.ts'

const entries = [
  { id: 'one', title: '第一章', level: 1, page: 5 },
  { id: 'group', title: '分组', level: 2, page: null },
  { id: 'section', title: '第一节', level: 3, page: 5 },
  { id: 'next', title: '第二节', level: 2, page: 8 },
  { id: 'two', title: '第二章', level: 1, page: 12 },
]
test('PDF chapter outline follows page boundaries and preserves nested groups', () => {
  assert.deepEqual(pdfChapterOutline(entries, 1).items, [])
  const first = pdfChapterOutline(entries, 7)
  assert.equal(first.activeId, 'section')
  assert.deepEqual(first.items.map(x => x.id), ['section'])
  assert.equal(first.items.find(x => x.id === 'section')?.parentId, null)
  assert.equal(pdfChapterOutline(entries, 11).activeId, 'next')
  assert.deepEqual(pdfChapterOutline(entries, 12).items.map(x => x.id), [])
  assert.deepEqual(pdfChapterOutline([], 1).items, [])
})

for (const level of [0, 1, 2, 3, 4]) {
  test(`PDF panes never overlap at split level ${level}, including auto`, () => {
    for (const page of [1, 5, 7, 8, 12]) {
      const result = pdfChapterOutline(entries, page, level)
      const left = new Set(result.toc.map(item => item.id))
      assert.ok(result.items.every(item => !left.has(item.id)))
      assert.ok(result.items.every(item => !('number' in item)))
    }
  })
}
