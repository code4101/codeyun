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
        <el-button
          v-if="note"
          type="primary"
          plain
          text
          circle
          :icon="CopyDocument"
          title="复制节点"
          :disabled="readonly"
          @click="showCopyDialog = true"
        />
        <el-button
          v-if="note"
          type="danger"
          plain
          text
          circle
          :icon="Delete"
          title="删除节点"
          :disabled="readonly"
          @click="deleteCurrentNote"
        />
        <slot name="actions" />
      </template>

      <template #meta-actions="{ readonly }">
        <el-tooltip content="根据标题和正文自动识别分类、形态、阶段" placement="top">
          <el-button
            size="small"
            :icon="MagicStick"
            :loading="aiCategorizing"
            :disabled="readonly || !currentNote"
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
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { CopyDocument, Delete, MagicStick } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import SharedNoteEditor from './SharedNoteEditor.vue';
import NoteCopyDialog from './NoteCopyDialog.vue';
import { useNoteStore, type NoteNode } from '@/api/notes';
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

const noteStore = useNoteStore();
const currentNote = ref<NoteNode | undefined>();
const isFetchingContent = ref(false);
const showCopyDialog = ref(false);
const aiCategorizing = ref(false);
let loadRequestToken = 0;

const hasConnections = computed(() => (currentNote.value?.edge_count ?? 0) > 0);
const hasOutConnections = computed(() => (currentNote.value?.out_degree ?? 0) > 0);

const toApiPatch = (patch: EditableNotePatch | Partial<NoteNode>) => {
  const outgoing = { ...patch } as Record<string, any>;
  if (typeof outgoing.start_at === 'number' && outgoing.start_at > 10000000000) outgoing.start_at /= 1000;
  return outgoing;
};

const openPlanetaryGraph = (mode: 'planetary' | 'satellite' = 'planetary') => {
  if (!currentNote.value) return;
  const suffix = mode === 'satellite' ? '卫星图' : '行星图';
  noteStore.addTab({
    id: `planet-${currentNote.value.id}-${mode}`,
    label: `${currentNote.value.title ? currentNote.value.title.slice(0, 8) : 'Untitled'} - ${suffix}`,
    type: 'planet',
    data: { noteId: currentNote.value.id, mode },
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
  currentNote.value = JSON.parse(JSON.stringify({
    ...note,
    content: detailed.content || '',
    private_level: detailed.private_level ?? note.private_level ?? 0
  }));
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

const handleSave = async (note: NoteNode, patch: EditableNotePatch = {}) => {
  const payload = Object.keys(patch).length ? patch : note;
  const updatedNote = await noteStore.updateNote(note.id, payload);
  if (!updatedNote) throw new Error('保存失败');
  emit('update', noteStore.getNoteById(note.id) || updatedNote);
  return updatedNote;
};

const handleSaveKeepalive = (note: NoteNode, patch: EditableNotePatch = {}) => {
  const payload = Object.keys(patch).length ? patch : note;
  putJsonKeepalive(`/api/notes/${encodeURIComponent(note.id)}`, toApiPatch(payload));
};

const handleEditorChange = (note: NoteNode) => {
  currentNote.value = JSON.parse(JSON.stringify(note));
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

    currentNote.value = JSON.parse(JSON.stringify(result.note));
    emit('update', result.note);
    ElMessage.success(result.summary || '已完成 AI 分类');
  } finally {
    aiCategorizing.value = false;
  }
};

const deleteCurrentNote = async () => {
  if (!currentNote.value) return;

  try {
    await ElMessageBox.confirm('确定要删除这个节点吗？', '警告', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning'
    });

    const noteId = currentNote.value.id;
    await noteStore.deleteNote(noteId);
    currentNote.value = undefined;
    showCopyDialog.value = false;
    emit('delete', noteId);
  } catch {
    // Ignore cancel
  }
};

const handleCopySuccess = (newNote: NoteNode) => {
  emit('create', newNote);
};
</script>

<style scoped>
 .note-detail-panel{display:flex;flex:1;flex-direction:column;min-height:0;overflow:hidden}
.graph-link-button{margin-left:10px}
</style>
