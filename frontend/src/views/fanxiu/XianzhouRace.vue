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
                        @change="(val: number) => handleCountChange(scope.row, val)"
                        :disabled="!canEdit"
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
                        @change="(val: number) => handleCountChange(scope.row, val)"
                        :disabled="!canEdit"
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
          empty-text="数据加载中..."
          class="editor-instance"
          @change="onEditorNoteChange"
          :readonly="!canEdit"
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
import type { NoteNode } from '@/api/notes';
import { useUserStore } from '@/store/userStore';

const userStore = useUserStore();

interface CharItem {
  name: string;
  note?: NoteNode;
  count: number;
}

const loading = ref(false);
const currentEditingNote = ref<NoteNode | undefined>(undefined);

const canEdit = computed(() => {
    if (!userStore.user) return false;
    // Allow '凡修手游' user OR superusers (admins)
    return userStore.user.username === '凡修手游' || userStore.isAdmin;
});

// Raw lists
const group1Names = ['天鹏祭司', '凌玉灵', '马良', '宝花'];
const group2Names = ['六道极圣', '乾老魔', '银月', '冰凤仙子', '黄泉鬼母', '南宫婉', '车老妖', '小极宫主'];

// Reactive Data
const group1Data = ref<CharItem[]>(group1Names.map(name => ({ name, count: 0 })));
const group2Data = ref<CharItem[]>(group2Names.map(name => ({ name, count: 0 })));

const LOCAL_STORAGE_KEY = 'fanxiu_xianzhou_counts';

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

const handleCountChange = async (row: CharItem, val: number) => {
    // 1. Update localStorage immediately
    saveLocalCounts();
    
    // 2. Update editor if open
    if (currentEditingNote.value && currentEditingNote.value.title === row.name) {
        currentEditingNote.value.weight = val;
    }

    // 3. Try to sync to backend if we have a note (best effort)
    if (row.note) {
        try {
            const updatedNote = await updateFanxiuChar(row.name, {
                weight: val
            });
            row.note = updatedNote;
            row.count = updatedNote.weight;
        } catch (e) {
            console.error('Failed to sync count to backend:', e);
            // We don't ElMessage.error here to keep it "pure frontend" feeling, 
            // but the console will show it.
        }
    }
};

const handleRowClick = async (row: CharItem) => {
    if (row.note) {
        currentEditingNote.value = JSON.parse(JSON.stringify(row.note));
    } else {
        // Auto create if not exists, to match StarNotes behavior
        try {
            const newNote = await updateFanxiuChar(row.name, {
                title: row.name,
                content: '',
                weight: row.count,
                node_type: 'memo',
                start_at: Date.now() / 1000
            });
            row.note = newNote;
            currentEditingNote.value = JSON.parse(JSON.stringify(newNote));
        } catch (e) {
            console.error(e);
            ElMessage.error('初始化笔记失败');
            return;
        }
    }
    
    // Auto scroll to editor
    setTimeout(() => {
        const editorEl = document.querySelector('.editor-section');
        if (editorEl) {
            editorEl.scrollIntoView({ behavior: 'smooth' });
        }
    }, 100);
};

const handleSave = async (note: NoteNode) => {
    try {
        const updatedNote = await updateFanxiuChar(note.title, note);
        
        const updateList = (list: CharItem[]) => {
            const item = list.find(i => i.name === note.title);
            if (item) {
                item.note = updatedNote;
                item.count = updatedNote.weight;
            }
        };
        
        updateList(group1Data.value);
        updateList(group2Data.value);
        
        if (currentEditingNote.value && !currentEditingNote.value.id) {
            currentEditingNote.value.id = updatedNote.id;
        }
        
    } catch (e) {
        throw e;
    }
};

const onEditorNoteChange = (note: NoteNode) => {
    const updateInList = (list: CharItem[]) => {
        const item = list.find(i => i.name === note.title);
        if (item) {
            item.count = note.weight;
        }
    };
    updateInList(group1Data.value);
    updateInList(group2Data.value);
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
