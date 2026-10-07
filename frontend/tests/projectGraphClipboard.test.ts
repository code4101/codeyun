import assert from 'node:assert/strict'
import test from 'node:test'
import { selectionObjects, preparePaste, insertPaste, removeCut, validateClipboard, clipboardHtml, parseClipboardHtml,
  setGraphClipboard, getGraphClipboard, type GraphClipboard } from '../src/plugins/modules/project-graph/graphClipboard.ts'
import { graphReferences } from '../src/plugins/modules/project-graph/gallery.ts'

const rect = (x: number, y: number) => ({ _: 'CollisionBox', shapes: [{ _: 'Rectangle', location: { _: 'Vector', x, y }, size: { _: 'Vector', x: 80, y: 40 } }] })
const graph = () => ({
  a: { _: 'TextNode', uuid: 'a', text: '中文 <节点>', details: [{ type: 'p', children: [{ text: '正文', bold: true }] }], collisionBox: rect(10, 20) },
  b: { _: 'ImageNode', uuid: 'b', attachmentId: 'image', collisionBox: rect(200, 20) },
  g: { _: 'Section', uuid: 'g', children: [{ $graphRef: 'a' }, { $graphRef: 'b' }], _collisionBoxNormal: rect(0, 0) },
  e: { _: 'StraightEdge', uuid: 'e', source: { $graphRef: 'a' }, target: { $graphRef: 'b' }, text: '依赖' },
  outside: { _: 'TextNode', uuid: 'outside', text: '外部', collisionBox: rect(500, 20) },
  crossing: { _: 'StraightEdge', uuid: 'crossing', source: { $graphRef: 'a' }, target: { $graphRef: 'outside' } },
  '@order': { value: ['a', 'b', 'g', 'e', 'outside', 'crossing'] },
})
const packet = (): GraphClipboard => ({ format: 'codeyun.pg-selection', version: 1,
  objects: selectionObjects(graph(), ['g']), attachments: { image: { type: 'image/png', data: 'YWJj' } }, tags: ['待办'], text: '中文 <节点>' })

test('selected group includes descendants and internal edges, excluding crossing edges and outside nodes', () => {
  const copied = packet()
  assert.deepEqual(copied.objects['@order'].value, ['a', 'b', 'g', 'e'])
  assert.deepEqual(copied.objects.a.details, graph().a.details)
  assert.deepEqual(selectionObjects(graph(), ['e'])['@order'].value, ['a', 'b', 'e'])
})
test('every paste gives nodes, edges, groups and assets fresh identities while preserving references and layout', () => {
  const copied = packet(), original = structuredClone(copied)
  let serial = 0
  const prepared = preparePaste(copied, { x: 1000, y: 500 }, () => `new-${++serial}`)
  const ids = prepared.objects['@order'].value
  assert.deepEqual(ids, ['new-1', 'new-2', 'new-3', 'new-4'])
  assert.deepEqual(graphReferences(prepared.objects['new-3']), ['new-1', 'new-2'])
  assert.deepEqual(graphReferences(prepared.objects['new-4']), ['new-1', 'new-2'])
  assert.equal(prepared.objects['new-2'].attachmentId, 'new-5')
  assert.equal(prepared.objects['new-3']._collisionBoxNormal.shapes[0].location.x, 860)
  const merged = insertPaste(graph(), prepared, () => 'png')
  assert.equal(merged['@attachment:new-5.png'].value, 'YWJj')
  assert.equal(merged.outside.uuid, 'outside')
  const again = preparePaste(copied, undefined, () => `new-${++serial}`)
  assert(!again.objects['@order'].value.some((id: string) => ids.includes(id)))
  assert.deepEqual(copied, original)
})
test('pasted identities do not inherit the source creation or edit dates', () => {
  const copied = packet(); copied.objects.a.createdAt = 1000; copied.objects.a.updatedAt = 2000;
  let serial = 0;
  const result = preparePaste(copied, undefined, () => `copy-${++serial}`);
  assert.equal(result.objects['copy-1'].createdAt, undefined);
  assert.equal(result.objects['copy-1'].updatedAt, undefined);
  assert.equal(copied.objects.a.createdAt, 1000);
})
test('HTML clipboard retains rich data and unicode while ordinary applications receive readable text', () => {
  const value = packet()
  assert.deepEqual(parseClipboardHtml(clipboardHtml(value)), value)
  assert.match(clipboardHtml(value), /<pre>中文 &lt;节点&gt;<\/pre>/)
  assert.equal(parseClipboardHtml('<p>普通文字</p>'), null)
})
test('host clipboard survives document replacement and is scoped to the account', () => {
  setGraphClipboard('user:1', packet())
  assert.deepEqual(getGraphClipboard('user:1'), packet())
  const read = getGraphClipboard('user:1')!
  read.objects.a.text = 'modified'
  assert.equal(getGraphClipboard('user:1')!.objects.a.text, '中文 <节点>')
  assert.equal(getGraphClipboard('user:2'), null)
})
test('cut removes copied descendants and crossing edges; cutting a child keeps its enclosing group valid', () => {
  const removed = removeCut(graph(), packet())
  assert.deepEqual(removed['@order'].value, ['outside'])
  const child = { ...packet(), objects: selectionObjects(graph(), ['a']) }
  const next = removeCut(graph(), child)
  assert.deepEqual(next.g.children, [{ $graphRef: 'b' }])
  assert(!next.e && !next.crossing)
  const changed = graph(); changed.a.text = 'changed'
  assert.throws(() => removeCut(changed, child), /未剪切/)
})
test('reject unsupported, incomplete or corrupt structured clipboard without degrading to plain text', () => {
  const value = packet()
  assert.throws(() => validateClipboard({ ...value, version: 2 }), /不支持/)
  assert.throws(() => validateClipboard({ ...value, attachments: {} }), /附件/)
  const bad = structuredClone(value); bad.objects.e.target.$graphRef = 'missing'
  assert.throws(() => validateClipboard(bad), /引用/)
  assert.throws(() => parseClipboardHtml('<div data-codeyun-pg-selection="e30="></div>'), /不支持/)
})
