<template>
  <div class="xianzhou-race-layout">
    <!-- Top Main Section -->
    <div class="main-section">
      <div class="page-header">
        <div class="header-left">
          <h2>仙舟竞速 - 伙伴清单</h2>
          <span class="header-tip">直接在表格中修改数量，修改后自动保存。点击行编辑笔记。</span>
        </div>
        <el-button type="primary" @click="refreshData" :loading="loading">刷新数据</el-button>
      </div>

      <div class="groups-container">
        <div class="group-column">
          <el-card class="group-card">
            <template #header>
              <div class="card-header">
                <span>第1组伙伴</span>
              </div>
            </template>
            <el-table 
              :data="group1Data" 
              @row-click="handleRowClick" 
              stripe 
              style="width: 100%" 
              row-key="name" 
              highlight-current-row
              :current-row-key="currentEditingNote?.title"
            >
              <el-table-column prop="name" label="人物" />
              <el-table-column label="数量" width="150" align="center">
                  <template #default="scope">
                      <el-input-number 
                        v-model="scope.row.count" 
                        :min="0" 
                        size="small"
                        style="width: 120px"
                        @click.stop
                        @change="(val: number | undefined) => handleCountChange(scope.row, val)"
                        :disabled="!canEditRow(scope.row)"
                      />
                  </template>
              </el-table-column>
            </el-table>
          </el-card>
        </div>

        <div class="group-column">
          <el-card class="group-card">
            <template #header>
               <div class="card-header">
                <span>第2组伙伴</span>
              </div>
            </template>
            <el-table 
              :data="group2Data" 
              @row-click="handleRowClick" 
              stripe 
              style="width: 100%" 
              row-key="name" 
              highlight-current-row
              :current-row-key="currentEditingNote?.title"
            >
              <el-table-column prop="name" label="人物" />
              <el-table-column label="数量" width="150" align="center">
                   <template #default="scope">
                      <el-input-number 
                        v-model="scope.row.count" 
                        :min="0" 
                        size="small"
                        style="width: 120px"
                        @click.stop
                        @change="(val: number | undefined) => handleCountChange(scope.row, val)"
                        :disabled="!canEditRow(scope.row)"
                      />
                  </template>
              </el-table-column>
            </el-table>
          </el-card>
        </div>
      </div>
    </div>

    <!-- Bottom Editor Section -->
    <div class="editor-section" :class="{ 'is-collapsed': !currentEditingNote }">
      <div v-if="currentEditingNote" class="editor-container">
        <UniversalNoteEditor
          :model-value="currentEditingNote"
          :on-save="handleSave"
          :on-save-keepalive="handleSaveKeepalive"
          empty-text="数据加载中..."
          class="editor-instance"
          @change="onEditorNoteChange"
          :readonly="currentEditingNote?.can_edit === false"
          :show-private-toggle="false"
          :lock-title="true"
          :lock-node-type="true"
          :lock-note-form="true"
        >
        </UniversalNoteEditor>
      </div>
      <div v-else class="empty-editor">
        <el-empty description="点击上方列表中的人物开始编辑笔记" :image-size="60" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { ElMessage } from 'element-plus';
import UniversalNoteEditor from '@/components/UniversalNoteEditor.vue';
import { getFanxiuChars, updateFanxiuChar } from '@/api/fanxiu';
import { useNoteStore, type NoteNode } from '@/api/notes';
import { useUserStore } from '@/store/userStore';
import { putJsonKeepalive } from '@/utils/keepaliveRequest';
import {
  deriveLegacySemanticsFromTaxonomy,
  NOTE_CATEGORY_DEFAULT,
  NOTE_FORM_MEMO,
  NOTE_KIND_FANXIU_CHAR,
  NOTE_LIFECYCLE_STAGE_DEFAULT,
  NOTE_WEIGHT_MODE_LINEAR
} from '@/utils/noteSemantics';

const userStore = useUserStore();
const noteStore = useNoteStore();

interface CharItem {
  name: string;
  note?: NoteNode;
  count: number;
}

const loading = ref(false);
const currentEditingNote = ref<NoteNode | undefined>(undefined);
const currentEditingCharName = ref('');

const canEdit = computed(() => {
    const notes = [...group1Data.value, ...group2Data.value]
      .map(item => item.note)
      .filter((note): note is NoteNode => Boolean(note));

    if (notes.length > 0) {
      return notes.some(note => note.can_edit !== false);
    }

    if (!userStore.user) return false;
    return userStore.user.username === '凡修手游' || userStore.isAdmin;
});

const canEditRow = (row: CharItem) => row.note ? row.note.can_edit !== false : canEdit.value;

// Raw lists
const group1Names = ['天鹏祭司', '凌玉灵', '马良', '宝花'];
const group2Names = ['六道极圣', '乾老魔', '银月', '冰凤仙子', '黄泉鬼母', '南宫婉', '车老妖', '小极宫主'];

// Reactive Data
const group1Data = ref<CharItem[]>(group1Names.map(name => ({ name, count: 0 })));
const group2Data = ref<CharItem[]>(group2Names.map(name => ({ name, count: 0 })));

const LOCAL_STORAGE_KEY = 'fanxiu_xianzhou_counts';
const FANXIU_NOTE_CATEGORIES = [{ key: NOTE_CATEGORY_DEFAULT, weight: 100 }];

const buildFanxiuTaxonomyPayload = (source: Partial<NoteNode> = {}) => {
    const taxonomy = deriveLegacySemanticsFromTaxonomy(
        FANXIU_NOTE_CATEGORIES,
        NOTE_CATEGORY_DEFAULT,
        NOTE_FORM_MEMO,
        NOTE_KIND_FANXIU_CHAR,
        source.lifecycle_stage ?? source.node_status ?? NOTE_LIFECYCLE_STAGE_DEFAULT
    );

    return {
        note_categories: taxonomy.note_categories,
        primary_category: taxonomy.primary_category,
        note_form: taxonomy.note_form,
        note_scene: taxonomy.note_scene,
        lifecycle_stage: taxonomy.lifecycle_stage,
        note_types: taxonomy.note_types,
        node_type: taxonomy.node_type,
        note_kind: taxonomy.note_kind,
        node_status: taxonomy.node_status
    };
};

const buildFanxiuNotePayload = (source: Partial<NoteNode> = {}) => ({
    ...source,
    ...buildFanxiuTaxonomyPayload(source),
    weight_mode: NOTE_WEIGHT_MODE_LINEAR
});

const loadLocalCounts = () => {
    try {
        const saved = localStorage.getItem(LOCAL_STORAGE_KEY);
        if (saved) {
            return JSON.parse(saved) as Record<string, number>;
        }
    } catch (e) {
        console.error('Failed to load local counts:', e);
    }
    return {};
};

const saveLocalCounts = () => {
    const counts: Record<string, number> = {};
    [...group1Data.value, ...group2Data.value].forEach(item => {
        counts[item.name] = item.count;
    });
    localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(counts));
};

const refreshData = async () => {
    loading.value = true;
    const localCounts = loadLocalCounts();
    
    try {
        const notes = await getFanxiuChars();
        
        // Map notes to groups
        const updateGroup = (group: CharItem[]) => {
            group.forEach(item => {
                const note = notes.find(n => n.title === item.name);
                if (note) {
                    item.note = note;
                    // If we have a local count, use it. Otherwise use backend.
                    item.count = localCounts[item.name] !== undefined ? localCounts[item.name] : note.weight;
                } else {
                    item.note = undefined;
                    // Use local count if exists, otherwise 0
                    item.count = localCounts[item.name] !== undefined ? localCounts[item.name] : 0;
                }
            });
        };
        
        updateGroup(group1Data.value);
        updateGroup(group2Data.value);
        
        // Save back to local in case we updated anything from backend
        saveLocalCounts();
        
    } catch (e) {
        console.error(e);
        // On error, we still have the local counts loaded into group data by above logic (implicitly)
        // Let's at least apply local counts if backend fails
        const applyLocalOnly = (group: CharItem[]) => {
            group.forEach(item => {
                if (localCounts[item.name] !== undefined) {
                    item.count = localCounts[item.name];
                }
            });
        };
        applyLocalOnly(group1Data.value);
        applyLocalOnly(group2Data.value);
        ElMessage.warning('从云端获取数据失败，当前显示本地缓存数据');
    } finally {
        loading.value = false;
    }
};

const handleCountChange = async (row: CharItem, val: number | undefined) => {
    const nextValue = val ?? 0;
    row.count = nextValue;
    saveLocalCounts();

    if (currentEditingCharName.value === row.name && currentEditingNote.value) {
        currentEditingNote.value.weight = nextValue;
    }

    if (row.note?.id && canEditRow(row)) {
        try {
            const updatedNote = await noteStore.updateNote(row.note.id, {
                weight: nextValue
            });
            if (!updatedNote) return;
            row.note = updatedNote;
            row.count = updatedNote.weight;
            if (currentEditingNote.value?.id === updatedNote.id) {
                currentEditingNote.value = JSON.parse(JSON.stringify(updatedNote));
            }
        } catch (e) {
            console.error('Failed to sync count to backend:', e);
        }
    }
};

const handleRowClick = async (row: CharItem) => {
    currentEditingCharName.value = row.name;
    if (row.note) {
        currentEditingNote.value = JSON.parse(JSON.stringify(row.note));
    } else {
        try {
            const newNote = await updateFanxiuChar(row.name, buildFanxiuNotePayload({
                title: row.name,
                content: '',
                weight: row.count,
                start_at: Date.now()
            }));
            row.note = newNote;
            currentEditingNote.value = JSON.parse(JSON.stringify(newNote));
        } catch (e) {
            console.error(e);
            ElMessage.error('初始化笔记失败');
            return;
        }
    }

    setTimeout(() => {
        const editorEl = document.querySelector('.editor-section');
        if (editorEl) {
            editorEl.scrollIntoView({ behavior: 'smooth' });
        }
    }, 100);
};

const handleSave = async (note: NoteNode, patch: Partial<NoteNode> = {}) => {
    const charName = currentEditingCharName.value || note.title;
    const syncRowNote = (updatedNote: NoteNode) => {
        const updateList = (list: CharItem[]) => {
            const item = list.find(i => (updatedNote.id && i.note?.id === updatedNote.id) || i.name === charName);
            if (item) {
                item.note = updatedNote;
                item.count = updatedNote.weight;
            }
        };
        updateList(group1Data.value);
        updateList(group2Data.value);
    };

    if (note.id) {
        const updatedNote = await noteStore.updateNote(note.id, buildFanxiuNotePayload({
            ...(Object.keys(patch).length ? patch : note),
            title: charName,
        }));
        if (!updatedNote) throw new Error('保存失败');
        syncRowNote(updatedNote);
        return updatedNote;
    }

    const createdNote = await updateFanxiuChar(charName, buildFanxiuNotePayload({
        ...(Object.keys(patch).length ? patch : note),
        title: charName
    }));
    syncRowNote(createdNote);
    return createdNote;
};

const handleSaveKeepalive = (note: NoteNode, patch: Partial<NoteNode> = {}) => {
    const charName = currentEditingCharName.value || note.title;
    const payload = buildFanxiuNotePayload({
        ...(Object.keys(patch).length ? patch : note),
        title: charName
    });

    if (note.id) {
        const normalizedPayload: Record<string, any> = { ...payload };
        if (typeof normalizedPayload.start_at === 'number' && normalizedPayload.start_at > 10000000000) {
            normalizedPayload.start_at /= 1000;
        }
        putJsonKeepalive(`/api/notes/${encodeURIComponent(note.id)}`, normalizedPayload);
        return;
    }

    const normalizedPayload: Record<string, any> = { ...payload };
    if (typeof normalizedPayload.start_at === 'number' && normalizedPayload.start_at > 10000000000) {
        normalizedPayload.start_at /= 1000;
    }
    putJsonKeepalive(`/api/fanxiu/chars/${encodeURIComponent(charName)}`, normalizedPayload);
};

const onEditorNoteChange = (note: NoteNode) => {
    const updateInList = (list: CharItem[]) => {
        const item = list.find(i => (note.id && i.note?.id === note.id) || i.name === currentEditingCharName.value);
        if (item) {
            item.note = note;
            item.count = note.weight;
        }
    };
    updateInList(group1Data.value);
    updateInList(group2Data.value);
    currentEditingNote.value = JSON.parse(JSON.stringify(note));
};

onMounted(() => {
    refreshData();
});

</script>

<style scoped>
.xianzhou-race-layout {
    display: flex;
    flex-direction: column;
    min-height: 100vh;
    background-color: #f5f7fa;
    overflow-x: hidden;
    overflow-y: auto;
}

.main-section {
    padding: 20px;
    border-bottom: 1px solid #e6e6e6;
    background-color: #f5f7fa;
}

.page-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
}

.header-left {
    display: flex;
    flex-direction: column;
    gap: 4px;
}

.header-tip {
    font-size: 12px;
    color: #909399;
}

.groups-container {
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
}

.group-column {
    flex: 1;
    min-width: 300px;
}

.card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
}

/* Bottom Editor Section */
.editor-section {
    flex: 1;
    background-color: #fff;
    display: flex;
    flex-direction: column;
    min-height: 600px;
}

.editor-section.is-collapsed {
    background-color: #fafafa;
}

.editor-container {
    display: flex;
    flex-direction: column;
}

.editor-instance {
    padding: 20px;
}

.empty-editor {
    flex: 1;
    display: flex;
    justify-content: center;
    align-items: center;
}

:deep(.el-table__row) {
    cursor: pointer;
}

:deep(.el-table__body tr.current-row > td.el-table__cell) {
    background-color: #ecf5ff !important;
}
</style>
