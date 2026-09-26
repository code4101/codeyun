import assert from 'node:assert/strict'
import test from 'node:test'
import { canDropDirectory, directoryDropPosition } from '../src/features/access/directoryDrag.ts'
import type { FeatureAccessTreeItem } from '../src/api/access.ts'

const node = (key: string, children: FeatureAccessTreeItem[] = [], container = false) => ({
  key, children, node_type: 'feature', can_have_children: container,
} as FeatureAccessTreeItem)

test('drop zones distinguish ordering from nesting, including empty containers', () => {
  const folder = node('folder', [], true)
  const page = node('page')
  assert.equal(directoryDropPosition(0.1, folder), 'before')
  assert.equal(directoryDropPosition(0.5, folder), 'inside')
  assert.equal(directoryDropPosition(0.9, folder), 'after')
  assert.equal(directoryDropPosition(0.1, page), 'before')
  assert.equal(directoryDropPosition(0.5, page), 'inside')
  assert.equal(directoryDropPosition(0.9, page), 'after')
  assert(canDropDirectory(page, folder, 'inside'))
  assert(canDropDirectory(folder, page, 'inside'))
})

test('subtrees cannot drop on themselves or descendants at any position', () => {
  const child = node('child')
  const parent = node('parent', [child], true)
  const sibling = node('sibling')
  for (const position of ['before', 'after', 'inside'] as const) {
    assert(!canDropDirectory(parent, parent, position))
    assert(!canDropDirectory(parent, child, position))
  }
  assert(canDropDirectory(child, sibling, 'before'))
  assert(canDropDirectory(child, parent, 'after'))
})
