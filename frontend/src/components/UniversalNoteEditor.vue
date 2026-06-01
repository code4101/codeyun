<template>
  <SharedNoteEditor
    :model-value="modelValue"
    :loading="loading"
    :readonly="readonly"
    :empty-text="emptyText"
    :show-private-toggle="showPrivateToggle"
    :lock-title="lockTitle"
    :lock-node-type="lockNodeType"
    :lock-note-form="lockNoteForm"
    :editor-layout="editorLayout"
    :editor-min-height="props.editorMinHeight"
    :draft-storage-key="draftStorageKey"
    :on-save="onSave"
    :on-save-keepalive="onSaveKeepalive"
    @update:modelValue="emit('update:modelValue', $event)"
    @change="emit('change', $event)"
  >
    <template #actions="slotProps">
      <slot name="actions" v-bind="slotProps" />
    </template>
    <template #meta-actions="slotProps">
      <slot name="meta-actions" v-bind="slotProps" />
    </template>
  </SharedNoteEditor>
</template>

<script setup lang="ts">
import type { NoteNode } from '@/api/notes';
import SharedNoteEditor from './SharedNoteEditor.vue';
import type { EditableNotePatch } from '@/utils/noteAutoSave';

const props = defineProps<{
  modelValue?: NoteNode;
  loading?: boolean;
  readonly?: boolean;
  emptyText?: string;
  showPrivateToggle?: boolean;
  lockTitle?: boolean;
  lockNodeType?: boolean;
  lockNoteForm?: boolean;
  editorLayout?: 'fill' | 'flow';
  editorMinHeight?: number;
  draftStorageKey?: string | null;
  onSave?: (note: NoteNode, patch?: EditableNotePatch) => Promise<NoteNode | void>;
  onSaveKeepalive?: (note: NoteNode, patch?: EditableNotePatch) => void;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', note: NoteNode): void;
  (e: 'change', note: NoteNode): void;
}>();
</script>
