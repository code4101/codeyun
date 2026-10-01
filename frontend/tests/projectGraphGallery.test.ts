import { test } from 'node:test'
import assert from 'node:assert/strict'
import { changeGallery, gallerySnapshot, selectedSubgraph } from '../src/plugins/modules/project-graph/gallery.ts'
import { differences, changed, type Objects } from '../src/collaboration/objectState.ts'

const node = (id: string) => ({ _: 'TextNode', uuid: id, text: id, details: [{ type: 'p', children: [{ text: 'body' }] }] })
const base = (): Objects => ({ a: node('a'), b: node('b'), '@order': { value: ['a', 'b'] } })

test('store/take is an identity-preserving move with reversible atomic deltas', () => {
  const before = base()
  const stored = changeGallery(before, { action: 'store', groupId: 'todo', selectedIds: ['a'] }, 'item', 10)
  assert.deepEqual(stored['@order'].value, ['b'])
  assert.equal(stored.a, undefined)
  assert.deepEqual(stored['@gallery:item:item'].objects.a, before.a)
  assert.equal(gallerySnapshot(stored).items[0].objectCount, 1)
  const delta = differences(before, stored)
  const undo = changed(stored, delta.map(change => ({ ...change, before: change.after, after: change.before })))
  assert.deepEqual(undo, before)
  const restored = changeGallery(stored, { action: 'take', itemId: 'item' })
  assert.equal(restored['@gallery:item:item'], undefined)
  assert.deepEqual(restored.a, before.a)
  assert.deepEqual(restored['@order'].value, ['b', 'a'])
  assert.throws(() => changeGallery(restored, { action: 'take', itemId: 'item' }), /不存在/)
})
test('internal edges accompany endpoints; crossing edges and partial parent groups block movement', () => {
  const objects = base()
  objects.e = { _: 'LineEdge', uuid: 'e', source: { $graphRef: 'a' }, target: { $graphRef: 'b' } }
  objects['@order'].value.push('e')
  assert.throws(() => selectedSubgraph(objects, ['a']), /连线/)
  assert.deepEqual(selectedSubgraph(objects, ['a', 'b']), ['a', 'b', 'e'])
  objects.g = { _: 'Section', uuid: 'g', children: [{ $graphRef: 'a' }, { $graphRef: 'b' }] }
  objects['@order'].value.push('g')
  assert.throws(() => selectedSubgraph(objects, ['a', 'b']), /分组/)
  assert.deepEqual(selectedSubgraph(objects, ['g']), ['a', 'b', 'e', 'g'])
})
test('custom groups, renames and moves preserve content; nonempty groups cannot be deleted', () => {
  let objects = changeGallery(base(), { action: 'create-group', title: '稍后' }, 'later')
  objects = changeGallery(objects, { action: 'store', groupId: 'later', selectedIds: ['a'] }, 'task')
  const original = objects['@gallery:item:task'].objects
  assert.throws(() => changeGallery(objects, { action: 'delete-group', groupId: 'later' }), /取回或移动/)
  objects = changeGallery(objects, { action: 'rename-item', itemId: 'task', title: '新标题' })
  objects = changeGallery(objects, { action: 'move', itemId: 'task', groupId: 'done' })
  objects = changeGallery(objects, { action: 'delete-group', groupId: 'later' })
  assert.deepEqual(objects['@gallery:item:task'].objects, original)
  assert.equal(objects['@gallery:item:task'].title, '新标题')
  assert.equal(objects['@gallery:item:task'].groupId, 'done')
  assert.equal(gallerySnapshot(objects).groups.some(group => group.id === 'later'), false)
})
test('duplicate takes and stale selections fail without mutating the source projection', () => {
  const before = base()
  assert.throws(() => changeGallery(before, { action: 'store', groupId: 'todo', selectedIds: ['missing'] }))
  const stored = changeGallery(before, { action: 'store', groupId: 'todo', selectedIds: ['a'] }, 'item')
  const duplicate = { ...stored, a: node('a'), '@order': { value: ['b', 'a'] } }
  assert.throws(() => changeGallery(duplicate, { action: 'take', itemId: 'item' }), /同一对象/)
  assert.deepEqual(before['@order'].value, ['a', 'b'])
})
