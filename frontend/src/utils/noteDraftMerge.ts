export const EDITABLE_NOTE_FIELD_NAMES = [
  'title',
  'content',
  'weight',
  'start_at',
  'note_categories',
  'primary_category',
  'note_form',
  'lifecycle_stage',
  'color',
  'private_level',
  'custom_fields'
] as const

export type EditableNoteFieldName = typeof EDITABLE_NOTE_FIELD_NAMES[number]

export type MergeableEditableNoteSnapshot = {
  id: string
} & Record<EditableNoteFieldName, unknown>

export interface EditableNoteDraftMergeResult<T extends MergeableEditableNoteSnapshot> {
  mergedSnapshot: T
  localChangedFields: EditableNoteFieldName[]
  remoteChangedFields: EditableNoteFieldName[]
  conflictingFields: EditableNoteFieldName[]
}

const cloneSnapshot = <T extends MergeableEditableNoteSnapshot>(snapshot: T): T => (
  JSON.parse(JSON.stringify(snapshot)) as T
)

const areFieldValuesEqual = <T extends MergeableEditableNoteSnapshot>(
  left: T,
  right: T,
  fieldName: EditableNoteFieldName,
) => {
  const leftValue = left[fieldName]
  const rightValue = right[fieldName]
  if (Array.isArray(leftValue) || Array.isArray(rightValue)) {
    return JSON.stringify(leftValue) === JSON.stringify(rightValue)
  }
  return leftValue === rightValue
}

export const getEditableNoteChangedFields = <T extends MergeableEditableNoteSnapshot>(
  left: T,
  right: T,
): EditableNoteFieldName[] => EDITABLE_NOTE_FIELD_NAMES.filter(
  fieldName => !areFieldValuesEqual(left, right, fieldName),
)

export const areEditableNoteSnapshotsEqual = <T extends MergeableEditableNoteSnapshot>(
  left: T,
  right: T,
) => left.id === right.id && getEditableNoteChangedFields(left, right).length === 0

/**
 * Three-way rebase for crash-recovery drafts.
 *
 * The baseline is the server snapshot originally observed by the editor.
 * Local and remote changes on independent fields survive automatically. A
 * conflict exists only when both sides changed the same field differently.
 */
export const mergeEditableNoteDraft = <T extends MergeableEditableNoteSnapshot>(
  draftSnapshot: T,
  draftBaselineSnapshot: T | null,
  serverSnapshot: T,
): EditableNoteDraftMergeResult<T> => {
  const baseline = draftBaselineSnapshot?.id === serverSnapshot.id
    ? draftBaselineSnapshot
    : null
  const localChangedFields = baseline
    ? getEditableNoteChangedFields(baseline, draftSnapshot)
    : getEditableNoteChangedFields(serverSnapshot, draftSnapshot)
  const remoteChangedFields = baseline
    ? getEditableNoteChangedFields(baseline, serverSnapshot)
    : [...localChangedFields]
  const remoteChangedFieldSet = new Set(remoteChangedFields)
  const conflictingFields = localChangedFields.filter(fieldName => (
    remoteChangedFieldSet.has(fieldName)
    && !areFieldValuesEqual(draftSnapshot, serverSnapshot, fieldName)
  ))
  const mergedSnapshot = cloneSnapshot(serverSnapshot)

  for (const fieldName of localChangedFields) {
    mergedSnapshot[fieldName] = JSON.parse(JSON.stringify(draftSnapshot[fieldName]))
  }

  return {
    mergedSnapshot,
    localChangedFields,
    remoteChangedFields,
    conflictingFields,
  }
}
