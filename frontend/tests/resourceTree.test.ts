import assert from 'node:assert/strict'
import test from 'node:test'
import { resourceRows, type ResourceNode } from '../src/components/resource-explorer/resourceTree.ts'

const file: ResourceNode = { id: 'file', name: '新建文本文档.txt', kind: 'file' }
const inner: ResourceNode = { id: 'inner', name: '嵌套2', kind: 'directory', children: [file] }
const outer: ResourceNode = { id: 'outer', name: '嵌套1', kind: 'directory', expanded: true, children: [inner] }

test('compact folders preserve resource identities and the file suffix', () => {
  const rows = resourceRows([outer])
  assert.deepEqual(rows.map(row => row.label), ['嵌套1 / 嵌套2', '新建文本文档.txt'])
  assert.deepEqual(rows[0]!.path.map(node => node.id), ['outer', 'inner'])
  assert.equal(rows[0]!.node.id, 'inner')
  assert.equal(rows[1]!.parentId, 'outer')
  assert.equal(rows[1]!.depth, 1)
  assert.equal(resourceRows([{ ...outer, expanded: false }]).length, 1)
})

test('empty folders have no synthetic child and branching folders are not compacted', () => {
  assert.equal(resourceRows([{ id: 'empty', name: '空目录', kind: 'directory', expanded: true, children: [] }]).length, 1)
  const rows = resourceRows([{ ...outer, children: [inner, file] }])
  assert.deepEqual(rows.map(row => row.label), ['嵌套1', '嵌套2', '新建文本文档.txt'])
})

test('semantic roots, unloaded folders and errors remain visible boundaries', () => {
  for (const boundary of [{ compact: false }, { loaded: false }, { loading: true }, { error: '读取失败' }]) {
    assert.equal(resourceRows([{ ...outer, ...boundary }])[0]!.label, '嵌套1')
  }
  assert.equal(resourceRows([outer], false)[0]!.label, '嵌套1')
  const loaded = { ...outer, children: [{ ...inner, loaded: false, children: undefined }] }
  assert.equal(resourceRows([loaded])[0]!.node.id, 'inner', 'load targets the last directory of the compact path')
})
