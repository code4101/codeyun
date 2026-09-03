import assert from 'node:assert/strict'
import test from 'node:test'

import {
  areEditableNoteSnapshotsEqual,
  mergeEditableNoteDraft,
  type MergeableEditableNoteSnapshot,
} from '../src/utils/noteDraftMerge.ts'

type EditableNoteSnapshot = MergeableEditableNoteSnapshot & {
  title: string
  content: string
  weight: number
  start_at: number
  note_categories: unknown[]
  primary_category: string | null
  note_form: string | null
  lifecycle_stage: string | null
  color: string | null
  private_level: number
  custom_fields: unknown[]
}

const snapshot = (overrides: Partial<EditableNoteSnapshot> = {}): EditableNoteSnapshot => ({
  id: 'note-1',
  title: '原始标题',
  content: '<p>原始正文</p>',
  weight: 0,
  start_at: 1_000,
  note_categories: [],
  primary_category: null,
  note_form: 'note',
  lifecycle_stage: 'note',
  color: null,
  private_level: 0,
  custom_fields: [],
  ...overrides,
})

test('stale draft without a local delta is discarded as recovery residue', () => {
  const baseline = snapshot()
  const draft = snapshot()
  const server = snapshot({ weight: 2 })

  const result = mergeEditableNoteDraft(draft, baseline, server)

  assert.deepEqual(result.localChangedFields, [])
  assert.deepEqual(result.remoteChangedFields, ['weight'])
  assert.deepEqual(result.conflictingFields, [])
  assert.equal(result.mergedSnapshot.weight, 2)
})

test('draft and server changes on different fields are rebased automatically', () => {
  const baseline = snapshot()
  const draft = snapshot({ title: '本地标题' })
  const server = snapshot({ weight: 3 })

  const result = mergeEditableNoteDraft(draft, baseline, server)

  assert.deepEqual(result.localChangedFields, ['title'])
  assert.deepEqual(result.remoteChangedFields, ['weight'])
  assert.deepEqual(result.conflictingFields, [])
  assert.equal(result.mergedSnapshot.title, '本地标题')
  assert.equal(result.mergedSnapshot.weight, 3)
})

test('only overlapping fields with different values are conflicts', () => {
  const baseline = snapshot()
  const draft = snapshot({ content: '<p>本地正文</p>', weight: 4 })
  const server = snapshot({ content: '<p>服务器正文</p>', title: '服务器标题' })

  const result = mergeEditableNoteDraft(draft, baseline, server)

  assert.deepEqual(result.conflictingFields, ['content'])
  assert.equal(result.mergedSnapshot.content, '<p>本地正文</p>')
  assert.equal(result.mergedSnapshot.title, '服务器标题')
  assert.equal(result.mergedSnapshot.weight, 4)
})

test('snapshot equality includes non-visible metadata fields', () => {
  assert.equal(areEditableNoteSnapshotsEqual(snapshot(), snapshot()), true)
  assert.equal(
    areEditableNoteSnapshotsEqual(snapshot(), snapshot({ primary_category: 'work' })),
    false,
  )
  assert.equal(areEditableNoteSnapshotsEqual(snapshot(), snapshot({ color: '#409eff' })), false)
})
