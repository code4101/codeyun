<template>
  <el-dialog
    v-model="visible"
    title="复制节点"
    width="600px"
    @open="initForm"
    append-to-body
  >
    <el-form :model="form" label-width="80px">
      <el-form-item label="标题">
        <el-input v-model="form.title" />
      </el-form-item>

      <el-form-item label="起始日期">
        <div class="date-row">
            <el-date-picker
              v-model="form.startDate"
              type="date"
              placeholder="选择日期"
              class="copy-date-picker"
              :clearable="false"
            />
            <SmartTimeInput
                v-model="form.startTime"
                :input-style="startTimeInputStyle"
            />
        </div>
        <div class="quick-actions">
            <span class="quick-label">基于源节点推移:</span>
            <el-button size="small" @click="shiftTime(1, 'month')">+1个月</el-button>
            <el-button size="small" @click="shiftTime(3, 'month')">+3个月</el-button>
            <el-button size="small" @click="shiftTime(1, 'year')">+1年</el-button>
        </div>
      </el-form-item>

      <el-form-item label="节点属性">
        <div class="props-row">
            <div class="prop-item">
                <span class="label">权重:</span>
                <el-input-number 
                  v-model="form.weight" 
                  :min="NOTE_WEIGHT_MIN"
                  :step="1" 
                  size="small"
                  controls-position="right"
                  class="weight-input"
                />
            </div>
            <div class="prop-item">
                <NoteTypeSelector
                  :model-value="form.noteCategories"
                  :legacy-type="form.primaryCategory"
                  :legacy-color="props.sourceNote.color ?? null"
                  label="分类"
                  :show-label="true"
                  :show-help-icon="false"
                  @update:model-value="handleCategoryChange"
                />
            </div>
            <div class="prop-item">
                <NodeSelector 
                  mode="form" 
                  v-model="form.noteForm"
                  :related-type="form.primaryCategory"
                  :custom-color="props.sourceNote.color ?? null"
                  :note-types="form.noteCategories"
                  label="形态"
                  :show-label="true"
                  :show-help-icon="false"
                  @change="handleFormChange"
                />
            </div>
            <div class="prop-item">
                <NodeSelector 
                  mode="status" 
                  v-model="form.lifecycleStage"
                  :related-type="form.primaryCategory"
                  :note-types="form.noteCategories"
                  label="阶段"
                  :show-label="true"
                  :show-help-icon="false"
                  @change="handleLifecycleStageChange"
                />
            </div>
        </div>
      </el-form-item>

      <el-form-item label="关联关系">
         <div class="link-checkboxes">
             <el-checkbox v-model="form.linkToNew" label="指向新节点 (源 -> 新)" />
             <el-checkbox v-model="form.linkFromNew" label="被新节点指向 (新 -> 源)" />
         </div>
      </el-form-item>

      <el-form-item label="内容">
        <div class="editor-wrapper">
            <NoteEditor
              v-model="form.content"
              mode="simple"
              layout="flow"
            />
        </div>
      </el-form-item>
    </el-form>
    
    <template #footer>
      <span class="dialog-footer">
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" @click="handleCopy" :loading="loading">确定复制</el-button>
      </span>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, reactive, defineAsyncComponent } from 'vue';
import { useNoteStore, type NoteNode } from '@/api/notes';
import { ElMessage } from 'element-plus';
import NodeSelector from './NodeSelector.vue';
import NoteTypeSelector from './NoteTypeSelector.vue';
import SmartTimeInput from './SmartTimeInput.vue';
import { NOTE_WEIGHT_DEFAULT, NOTE_WEIGHT_MIN, normalizeNoteWeight } from '@/utils/noteWeight';
import { derivePrimaryNodeType, normalizeNoteTypeAssignments, type NoteTypeAssignment } from '@/utils/nodeConfig';
import {
  deriveLegacySemanticsFromTaxonomy,
  NOTE_CATEGORY_DEFAULT,
  NOTE_FORM_DEFAULT,
  NOTE_LIFECYCLE_STAGE_DEFAULT,
  NOTE_SCENE_DEFAULT
} from '@/utils/noteSemantics';

const NoteEditor = defineAsyncComponent(() => import('./NoteEditor.vue'));

const props = defineProps<{
  modelValue: boolean;
  sourceNote: NoteNode;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void;
  (e: 'success', newNote: NoteNode): void;
}>();

const noteStore = useNoteStore();
const loading = ref(false);

const visible = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
});

const form = reactive({
  title: '',
  content: '',
  startDate: new Date(),
  startTime: '00:00:00',
  weight: NOTE_WEIGHT_DEFAULT,
  nodeType: 'note',
  noteTypes: [] as NoteTypeAssignment[],
  primaryCategory: NOTE_CATEGORY_DEFAULT,
  noteCategories: [] as NoteTypeAssignment[],
  noteForm: NOTE_FORM_DEFAULT,
  lifecycleStage: NOTE_LIFECYCLE_STAGE_DEFAULT,
  completionProgressExpr: '',
  noteScene: NOTE_SCENE_DEFAULT,
  linkToNew: true,
  linkFromNew: false
});

const startTimeInputStyle = {
  width: '120px'
};

const initForm = () => {
  if (!props.sourceNote) return;
  
  form.title = props.sourceNote.title;
  form.content = props.sourceNote.content || '';
  form.weight = normalizeNoteWeight(props.sourceNote.weight);
  form.nodeType = props.sourceNote.node_type || 'note';
  form.noteTypes = normalizeNoteTypeAssignments(props.sourceNote.note_types, form.nodeType);
  form.primaryCategory = props.sourceNote.primary_category || NOTE_CATEGORY_DEFAULT;
  form.noteCategories = normalizeNoteTypeAssignments(props.sourceNote.note_categories, form.primaryCategory);
  form.noteForm = props.sourceNote.note_form || NOTE_FORM_DEFAULT;
  form.lifecycleStage = props.sourceNote.lifecycle_stage || NOTE_LIFECYCLE_STAGE_DEFAULT;
  form.completionProgressExpr = props.sourceNote.completion_progress_expr || '';
  form.noteScene = props.sourceNote.note_scene || props.sourceNote.note_kind || NOTE_SCENE_DEFAULT;
  
  const startAt = new Date(props.sourceNote.start_at);
  form.startDate = startAt;
  form.startTime = formatTimeStr(startAt);
  
  form.linkToNew = true;
  form.linkFromNew = false;
};

const formatTimeStr = (date: Date) => {
    const h = String(date.getHours()).padStart(2, '0');
    const m = String(date.getMinutes()).padStart(2, '0');
    const s = String(date.getSeconds()).padStart(2, '0');
    return `${h}:${m}:${s}`;
};

const shiftTime = (amount: number, unit: 'month' | 'year') => {
    // 基于当前表单的时间进行推移
    const baseDate = new Date(form.startDate);
    
    if (unit === 'month') {
        baseDate.setMonth(baseDate.getMonth() + amount);
    } else if (unit === 'year') {
        baseDate.setFullYear(baseDate.getFullYear() + amount);
    }
    
    form.startDate = baseDate;
    // 时间部分保持不变
};

const syncLegacyFieldsFromTaxonomy = () => {
    const legacy = deriveLegacySemanticsFromTaxonomy(
        form.noteCategories,
        form.primaryCategory || NOTE_CATEGORY_DEFAULT,
        form.noteForm || NOTE_FORM_DEFAULT,
        form.noteScene || NOTE_SCENE_DEFAULT,
        form.lifecycleStage || NOTE_LIFECYCLE_STAGE_DEFAULT
    );
    form.noteCategories = normalizeNoteTypeAssignments(legacy.note_categories, legacy.primary_category);
    form.primaryCategory = legacy.primary_category;
    form.noteForm = legacy.note_form;
    form.lifecycleStage = legacy.lifecycle_stage;
    form.noteScene = legacy.note_scene;
    form.noteTypes = normalizeNoteTypeAssignments(legacy.note_types, legacy.node_type);
    form.nodeType = legacy.node_type;
};

const handleCategoryChange = (value: NoteTypeAssignment[]) => {
    form.noteCategories = normalizeNoteTypeAssignments(value, form.primaryCategory);
    form.primaryCategory = derivePrimaryNodeType(form.noteCategories, form.primaryCategory);
    syncLegacyFieldsFromTaxonomy();
};

const handleFormChange = (value: string) => {
    form.noteForm = value || NOTE_FORM_DEFAULT;
    syncLegacyFieldsFromTaxonomy();
};

const handleLifecycleStageChange = (value: string) => {
    form.lifecycleStage = value || NOTE_LIFECYCLE_STAGE_DEFAULT;
    syncLegacyFieldsFromTaxonomy();
};

const handleCopy = async () => {
    loading.value = true;
    try {
        // Combine date and time
        const finalDate = new Date(form.startDate);
        const [h, m, s] = form.startTime.split(':').map(Number);
        if (!isNaN(h)) finalDate.setHours(h);
        if (!isNaN(m)) finalDate.setMinutes(m);
        if (!isNaN(s)) finalDate.setSeconds(s);
        
        const newNote = await noteStore.createNote(
            form.title,
            form.content,
            form.weight,
            finalDate.getTime(),
            form.nodeType,
            form.lifecycleStage,
            [],
            props.sourceNote.private_level ?? 0,
            props.sourceNote.color ?? null,
            props.sourceNote.weight_mode ?? null,
            form.noteScene,
            form.noteTypes,
            form.noteCategories,
            form.primaryCategory,
            form.noteForm,
            form.noteScene,
            form.lifecycleStage,
            form.completionProgressExpr || null
        );
        
        if (newNote) {
            // Handle links
            const p: Promise<any>[] = [];
            if (form.linkToNew) {
                // Source -> New
                p.push(noteStore.createEdge(props.sourceNote.id, newNote.id));
            }
            if (form.linkFromNew) {
                // New -> Source
                p.push(noteStore.createEdge(newNote.id, props.sourceNote.id));
            }
            
            if (p.length > 0) {
                await Promise.all(p);
            }
            
            ElMessage.success('复制成功');
            emit('success', newNote);
            visible.value = false;
        }
    } catch (e) {
        console.error(e);
        ElMessage.error('复制失败');
    } finally {
        loading.value = false;
    }
};
</script>

<style scoped>
.date-row {
    display: flex;
    align-items: center;
    gap: 10px;
}

.copy-date-picker {
    width: 140px;
}

.quick-actions {
    margin-top: 8px;
    display: flex;
    align-items: center;
    gap: 8px;
}

.quick-label {
    font-size: 12px;
    color: #909399;
}

.props-row {
    display: flex;
    align-items: center;
    gap: 15px;
    flex-wrap: wrap;
}

.prop-item {
    display: flex;
    align-items: center;
    gap: 5px;
}

.prop-item .label {
    font-size: 12px;
    color: #606266;
}

.weight-input {
    width: 100px;
}

.link-checkboxes {
    display: flex;
    flex-direction: column;
    gap: 5px;
}

.editor-wrapper {
    width: 100%;
    /* Override NoteEditor default height to be more compact in dialog */
    :deep(.editor-container) {
        min-height: 200px !important;
    }
    :deep(.w-e-text-container) {
        min-height: 200px !important;
    }
}
</style>
