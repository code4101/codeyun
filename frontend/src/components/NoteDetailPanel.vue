<template>
  <div class="note-detail-panel">
    <SharedNoteEditor
      :model-value="currentNote"
      :loading="Boolean(props.noteId) && isFetchingContent"
      :empty-text="props.noteId ? '节点未就绪' : '未选择节点'"
      :readonly="currentNote?.can_edit === false"
      :show-private-toggle="true"
      :editor-layout="props.editorLayout"
      :on-save="handleSave"
      :on-save-keepalive="handleSaveKeepalive"
      @change="handleEditorChange"
    >
      <template #actions="{ note, readonly }">
        <NoteTitleActions
          v-if="note"
          :readonly="readonly"
          :doc-href="resolveDocHref(note)"
          :show-doc-link="userStore.isAuthenticated"
          :show-share="userStore.isAuthenticated"
          :can-share="userStore.isAuthenticated && canManageDocAccess"
          :can-copy="!readonly"
          :can-delete="!readonly"
          @share="openShareDialog"
          @copy="showCopyDialog = true"
          @delete="deleteCurrentNote"
        />
        <slot name="actions" />
      </template>

      <template #meta-actions="{ readonly }">
        <el-tooltip content="根据当前标题，并参考已有条目元数据自动识别分类、形态、阶段" placement="top">
          <el-button
            size="small"
            :icon="MagicStick"
            :loading="aiCategorizing"
            :disabled="readonly || !currentNote || !userStore.isAuthenticated"
            @click="categorizeCurrentNote"
          >
            AI分类
          </el-button>
        </el-tooltip>
        <el-tooltip content="全景图：展示该节点所在的完整关联网络" placement="top">
          <el-button
            size="small"
            class="graph-link-button"
            :disabled="readonly || !hasConnections"
            @click="openPlanetaryGraph('planetary')"
          >
            行星图
          </el-button>
        </el-tooltip>
        <el-tooltip content="衍生图：仅展示该节点向下延伸的发展网络（忽略来源）" placement="top">
          <el-button
            size="small"
            :disabled="readonly || !hasOutConnections"
            @click="openPlanetaryGraph('satellite')"
          >
            卫星图
          </el-button>
        </el-tooltip>
      </template>
    </SharedNoteEditor>

    <NoteCopyDialog
      v-if="currentNote"
      v-model="showCopyDialog"
      :source-note="currentNote"
      @success="handleCopySuccess"
    />

    <NoteDocAccessDialog
      v-if="currentNote"
      v-model="showAccessDialog"
      :note-ref="getDocRouteRef(currentNote)"
      :title="currentNote.title"
      @update:access="handleAccessUpdate"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { MagicStick } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import SharedNoteEditor from './SharedNoteEditor.vue';
import NoteCopyDialog from './NoteCopyDialog.vue';
import NoteDocAccessDialog from './NoteDocAccessDialog.vue';
import NoteTitleActions from './NoteTitleActions.vue';
import { noteKey, useNoteStore, type NoteDocResourceAccess, type NoteNode } from '@/api/notes';
import { useUserStore } from '@/store/userStore';
import { putJsonKeepalive } from '@/utils/keepaliveRequest';
import type { EditableNotePatch } from '@/utils/noteAutoSave';

const props = withDefaults(defineProps<{
  noteId: string;
  editorLayout?: 'fill' | 'flow';
}>(), {
  editorLayout: 'fill'
});

const emit = defineEmits<{
  (e: 'update', note: NoteNode): void;
  (e: 'delete', noteId: string): void;
  (e: 'create', note: NoteNode): void;
}>();

const router = useRouter();
const noteStore = useNoteStore();
const userStore = useUserStore();
const currentNote = ref<NoteNode | undefined>();
const isFetchingContent = ref(false);
const showCopyDialog = ref(false);
const showAccessDialog = ref(false);
const aiCategorizing = ref(false);
let loadRequestToken = 0;

const hasConnections = computed(() => (currentNote.value?.edge_count ?? 0) > 0);
const hasOutConnections = computed(() => (currentNote.value?.out_degree ?? 0) > 0);
const canManageDocAccess = computed(() => (
  currentNote.value
    ? (currentNote.value.access?.capabilities.can_manage_access ?? currentNote.value.can_edit !== false)
    : false
));

const getDocRouteRef = (note: Pick<NoteNode, 'id' | 'numeric_id'>) => (
  note.numeric_id && note.numeric_id > 0 ? String(note.numeric_id) : noteKey(note.id)
);
const resolveDocHref = (note: Pick<NoteNode, 'id' | 'numeric_id'>) => (
  router.resolve(`/doc/${encodeURIComponent(getDocRouteRef(note))}`).href
);

const toApiPatch = (patch: EditableNotePatch | Partial<NoteNode>) => {
  const outgoing = { ...patch } as Record<string, any>;
  if (typeof outgoing.start_at === 'number' && outgoing.start_at > 10000000000) outgoing.start_at /= 1000;
  return outgoing;
};

const cloneNoteForDetail = (note: NoteNode): NoteNode => JSON.parse(JSON.stringify({
  ...note,
  content: note.content || '',
  private_level: note.private_level ?? 0
}));

const openPlanetaryGraph = (mode: 'planetary' | 'satellite' = 'planetary') => {
  if (!currentNote.value) return;
  const suffix = mode === 'satellite' ? '卫星图' : '行星图';
  noteStore.addTab({
    id: `planet-${noteKey(currentNote.value.id)}-${mode}`,
    label: `${currentNote.value.title ? currentNote.value.title.slice(0, 8) : 'Untitled'} - ${suffix}`,
    type: 'planet',
    data: { noteId: noteKey(currentNote.value.id), mode },
    closable: true
  });
};

const loadNote = async (id: string, requestToken: number) => {
  showCopyDialog.value = false;
  currentNote.value = undefined;
  isFetchingContent.value = true;

  const detailed = await noteStore.fetchNoteDetail(id);
  if (requestToken !== loadRequestToken || props.noteId !== id) return;

  isFetchingContent.value = false;
  if (!detailed) {
    ElMessage.error('无法加载节点详情');
    return;
  }

  const note = noteStore.getNoteById(id) || detailed;
  currentNote.value = cloneNoteForDetail({
    ...note,
    content: detailed.content || '',
    private_level: detailed.private_level ?? note.private_level ?? 0
  });
};

watch(() => props.noteId, async newId => {
  const requestToken = ++loadRequestToken;

  if (!newId) {
    currentNote.value = undefined;
    isFetchingContent.value = false;
    showCopyDialog.value = false;
    return;
  }

  await loadNote(newId, requestToken);
}, { immediate: true });

watch(
  () => {
    if (!props.noteId || !currentNote.value || noteKey(currentNote.value.id) !== props.noteId) return null;
    const note = noteStore.getNoteById(props.noteId);
    if (!note || note.content === undefined) return null;
    return [
      note.id,
      note.updated_at,
      note.title,
      note.content,
      JSON.stringify(note.custom_fields ?? []),
      note.weight,
      note.private_level,
      note.primary_category,
      note.note_form,
      note.lifecycle_stage,
      note.completion_progress_expr
    ].join('\u0001');
  },
  () => {
  if (!props.noteId || !currentNote.value || noteKey(currentNote.value.id) !== props.noteId) return;
    const note = noteStore.getNoteById(props.noteId);
    if (!note || note.content === undefined) return;

    currentNote.value = cloneNoteForDetail({
      ...currentNote.value,
      ...note,
      content: note.content || '',
      private_level: note.private_level ?? currentNote.value.private_level ?? 0
    });
  }
);

const refreshCurrentNoteFromServer = async () => {
  if (!props.noteId || isFetchingContent.value) return;
  const requestToken = loadRequestToken;
  const detailed = await noteStore.fetchNoteDetail(props.noteId, { force: true });
  if (!detailed || requestToken !== loadRequestToken || props.noteId !== noteKey(detailed.id) || !currentNote.value) return;

  const note = noteStore.getNoteById(props.noteId) || detailed;
  currentNote.value = cloneNoteForDetail({
    ...currentNote.value,
    ...note,
    content: detailed.content || '',
    private_level: detailed.private_level ?? note.private_level ?? currentNote.value.private_level ?? 0
  });
};

const handleVisibilityChange = () => {
  if (document.visibilityState === 'visible') {
    void refreshCurrentNoteFromServer();
  }
};

onMounted(() => {
  window.addEventListener('focus', refreshCurrentNoteFromServer);
  document.addEventListener('visibilitychange', handleVisibilityChange);
});

onBeforeUnmount(() => {
  window.removeEventListener('focus', refreshCurrentNoteFromServer);
  document.removeEventListener('visibilitychange', handleVisibilityChange);
});

const handleSave = async (note: NoteNode, patch: EditableNotePatch = {}) => {
  const payload = Object.keys(patch).length ? patch : note;
  const updatedNote = await noteStore.updateNote(note.id, payload);
  if (!updatedNote) throw new Error('保存失败');
  emit('update', noteStore.getNoteById(note.id) || updatedNote);
  return updatedNote;
};

const handleSaveKeepalive = (note: NoteNode, patch: EditableNotePatch = {}) => {
  const payload = Object.keys(patch).length ? patch : note;
  putJsonKeepalive(`/api/notes/${encodeURIComponent(noteKey(note.id))}`, toApiPatch(payload));
};

const handleEditorChange = (note: NoteNode) => {
  currentNote.value = cloneNoteForDetail(note);
  emit('update', note);
};

const categorizeCurrentNote = async () => {
  if (!currentNote.value || aiCategorizing.value) {
    return;
  }

  aiCategorizing.value = true;
  try {
    const result = await noteStore.aiCategorizeNote(currentNote.value.id);
    if (!result) {
      return;
    }

    currentNote.value = cloneNoteForDetail(result.note);
    emit('update', result.note);
    ElMessage.success(result.summary || '已完成 AI 分类');
  } finally {
    aiCategorizing.value = false;
  }
};

const openShareDialog = () => {
  if (!currentNote.value) return;
  if (!canManageDocAccess.value) {
    ElMessage.warning('没有权限管理该文档');
    return;
  }
  showAccessDialog.value = true;
};

const deleteCurrentNote = async () => {
  if (!currentNote.value) return;

  try {
    await ElMessageBox.confirm('确定要删除这个节点吗？', '警告', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning'
    });

    const noteId = noteKey(currentNote.value.id);
    const deleted = await noteStore.deleteNote(noteId);
    if (!deleted) return;
    currentNote.value = undefined;
    showCopyDialog.value = false;
    showAccessDialog.value = false;
    emit('delete', noteId);
  } catch {
    // Ignore cancel
  }
};

const handleCopySuccess = (newNote: NoteNode) => {
  emit('create', newNote);
};

const handleAccessUpdate = (access: NoteDocResourceAccess) => {
  if (!currentNote.value) return;
  currentNote.value = cloneNoteForDetail({
    ...currentNote.value,
    access,
    can_edit: access.capabilities.can_edit_content
  });
  emit('update', currentNote.value);
};
</script>

<style scoped>
 .note-detail-panel{display:flex;flex:1;flex-direction:column;min-height:0;overflow:hidden}
.graph-link-button{margin-left:10px}
</style>
