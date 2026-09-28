import assert from 'node:assert/strict'
import test from 'node:test'
import { hasPlateContent, storedPlateValue } from '../src/components/rich-text/plateValue.ts'

test('an explicit empty container survives PG empty-body normalization and serialization', () => {
  const value = [{ type: 'codeyun-collapse', title: '', collapsed: true,
    children: [{ type: 'p', children: [{ text: '' }] }] }]
  assert.equal(hasPlateContent(value), true)
  assert.deepEqual(storedPlateValue(JSON.parse(JSON.stringify(value))), value)
  assert.deepEqual(storedPlateValue([{ type: 'p', children: [{ text: '' }] }]), [])
})
