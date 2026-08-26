import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildRecognitionOpsTree,
  formatAmbiguitySelectionCounts,
} from '../src/standard/fanxiu/data-annotation/recognitionOpsModel.ts'


test('recognition ops keeps identity ambiguity in the existing category tree', () => {
  const tree = buildRecognitionOpsTree(
    [{ id: 'identity_ambiguity', label: '身份并列', count: 1 }],
    [{ id: 'ambiguity:abc', category: 'identity_ambiguity', label: '#3 / #9 · 7 次' }],
  )

  assert.deepEqual(tree, [
    {
      id: 'category:identity_ambiguity',
      label: '身份并列 1',
      type: 'category',
      children: [
        {
          id: 'issue:ambiguity:abc',
          label: '#3 / #9 · 7 次',
          type: 'issue',
          issueId: 'ambiguity:abc',
        },
      ],
    },
  ])
})


test('ambiguity fallback distribution distinguishes unresolved observations', () => {
  assert.equal(
    formatAmbiguitySelectionCounts({ 9: 6, 3: 2, unresolved: 1 }),
    '#3 2次 / #9 6次 / 未解决 1次',
  )
})
