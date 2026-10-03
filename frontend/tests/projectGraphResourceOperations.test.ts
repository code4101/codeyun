import assert from 'node:assert/strict'
import test from 'node:test'
import { canMoveResource, canManageResource, resourceDestination } from '../src/plugins/modules/project-graph/resourceOperations.ts'

const folders = [{ id: '1', title: 'parent', parentId: '' }, { id: '2', title: 'child', parentId: '1' }, { id: '3', title: 'other', parentId: '' }]
const documents = [{ id: '10', title: 'file', folderId: '1', role: 'manager' }, { id: '11', title: 'shared', folderId: '', role: 'viewer' }]
const file = { id: 'file:10', name: 'file.prg', kind: 'file' }
const folder = id => ({ id: `folder:${id}`, name: id, kind: 'directory' })

test('move files, directories and root; file targets mean their actual parent', () => {
  assert.equal(resourceDestination(file, documents), '1')
  assert.equal(canMoveResource(file, folder('1'), documents, folders), false)
  assert.equal(canMoveResource(file, null, documents, folders), true)
  assert.equal(canMoveResource(file, folder('3'), documents, folders), true)
  assert.equal(canMoveResource(folder('2'), file, documents, folders), false)
  assert.equal(canMoveResource(folder('2'), folder('3'), documents, folders), true)
  assert.equal(canMoveResource(folder('2'), null, documents, folders), true)
})

test('reject directory cycles, missing destinations and resources without management permission', () => {
  assert.equal(canMoveResource(folder('1'), folder('1'), documents, folders), false)
  assert.equal(canMoveResource(folder('1'), folder('2'), documents, folders), false)
  assert.equal(canMoveResource(file, folder('missing'), documents, folders), false)
  assert.equal(canManageResource({ id: 'file:11', kind: 'file' }, documents, folders), false)
})
