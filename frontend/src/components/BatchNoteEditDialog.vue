<template>
  <el-dialog
    :model-value="modelValue"
    title="批量编辑"
    width="560px"
    destroy-on-close
    append-to-body
    @update:model-value="value => emit('update:modelValue', value)"
  >
    <div class="batch-edit-dialog">
      <div class="summary-line">将修改 {{ selectedCount }} 个节点</div>

      <div class="field-list">
        <div class="field-row">
          <el-checkbox v-model="form.applyCategories" class="field-toggle">分类</el-checkbox>
          <div class="field-control">
            <NoteTypeSelector
              :model-value="form.noteCategories"
              :legacy-type="form.primaryCategory"
              :legacy-color="null"
              :show-label="false"
              :show-help-icon="false"
              :disabled="!form.applyCategories"
              @update:model-value="handleCategoryChange"
            />
          </div>
        </div>

        <div class="field-row">
          <el-checkbox v-model="form.applyNoteForm" class="field-toggle">形态</el-checkbox>
          <div class="field-control">
            <NodeSelector
              mode="form"
              v-model="form.noteForm"
              :related-type="form.primaryCategory"
              :note-types="form.noteCategories"
              :show-label="false"
              :show-help-icon="false"
              :disabled="!form.applyNoteForm"
            />
          </div>
        </div>

        <div class="field-row">
          <el-checkbox v-model="form.applyLifecycleStage" class="field-toggle">阶段</el-checkbox>
          <div class="field-control">
            <NodeSelector
              mode="status"
              v-model="form.lifecycleStage"
              :related-type="form.primaryCategory"
              :note-types="form.noteCategories"
              :show-label="false"
              :show-help-icon="false"
              :disabled="!form.applyLifecycleStage"
            />
          </div>
        </div>

        <div class="field-row">
          <el-checkbox v-model="form.applyPrivateLevel" class="field-toggle">私密</el-checkbox>
          <div class="field-control compact-control">
            <el-input-number
              v-model="form.privateLevel"
              :min="0"
              :step="1"
              size="small"
              controls-position="right"
              class="number-input"
              :disabled="!form.applyPrivateLevel"
            />
          </div>
        </div>

        <div class="field-row">
          <el-checkbox v-model="form.applyWeight" class="field-toggle">权重</el-checkbox>
          <div class="field-control weight-row">
            <el-select
              v-model="form.weightMode"
              size="small"
              class="weight-mode-select"
              :disabled="!form.applyWeight"
            >
              <el-option label="设为" value="set" />
              <el-option label="增减" value="delta" />
            </el-select>
            <el-input-number
              v-model="form.weight"
              :min="NOTE_WEIGHT_MIN"
              :step="1"
              size="small"
              controls-position="right"
              class="number-input"
              :disabled="!form.applyWeight"
            />
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="dialog-footer">
        <el-button @click="emit('update:modelValue', false)">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSubmit">应用修改</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { useNoteStore, type NoteBatchUpdateResponse } from '@/api/notes';
import NoteTypeSelector from './NoteTypeSelector.vue';
import NodeSelector from './NodeSelector.vue';
import {
  derivePrimaryCategory,
  normalizeNoteCategories,
  NOTE_CATEGORY_DEFAULT,
  NOTE_CATEGORY_UNCATEGORIZED,
  NOTE_FORM_DEFAULT,
  NOTE_LIFECYCLE_STAGE_DEFAULT,
  type NoteTypeAssignment
} from '@/utils/noteSemantics';
import { NOTE_WEIGHT_MIN, normalizeNoteWeight } from '@/utils/noteWeight';

const props = defineProps<{
  modelValue: boolean;
  noteIds: string[];
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void;
  (e: 'saved', result: NoteBatchUpdateResponse): void;
}>();

const noteStore = useNoteStore();

const selectedCount = computed(() => props.noteIds.length);
const saving = computed(() => noteStore.loading);

const createDefaultCategories = (): NoteTypeAssignment[] => [{ key: NOTE_CATEGORY_UNCATEGORIZED, weight: 100 }];

const form = reactive({
  applyCategories: false,
  noteCategories: createDefaultCategories(),
  primaryCategory: NOTE_CATEGORY_UNCATEGORIZED,
  applyNoteForm: false,
  noteForm: NOTE_FORM_DEFAULT,
  applyLifecycleStage: false,
  lifecycleStage: NOTE_LIFECYCLE_STAGE_DEFAULT,
  applyPrivateLevel: false,
  privateLevel: 0,
  applyWeight: false,
  weightMode: 'set' as 'set' | 'delta',
  weight: 0,
});

const resetForm = () => {
  form.applyCategories = false;
  form.noteCategories = createDefaultCategories();
  form.primaryCategory = NOTE_CATEGORY_UNCATEGORIZED;
  form.applyNoteForm = false;
  form.noteForm = NOTE_FORM_DEFAULT;
  form.applyLifecycleStage = false;
  form.lifecycleStage = NOTE_LIFECYCLE_STAGE_DEFAULT;
  form.applyPrivateLevel = false;
  form.privateLevel = 0;
  form.applyWeight = false;
  form.weightMode = 'set';
  form.weight = 0;
};

watch(() => props.modelValue, value => {
  if (value) resetForm();
});

const handleCategoryChange = (value: NoteTypeAssignment[]) => {
  form.noteCategories = normalizeNoteCategories(value, form.primaryCategory);
  form.primaryCategory = derivePrimaryCategory(form.noteCategories, form.primaryCategory);
};

const buildPatch = () => {
  const patch: Record<string, unknown> = {};

  if (form.applyCategories) {
    const normalizedCategories = normalizeNoteCategories(form.noteCategories, form.primaryCategory);
    patch.note_categories = normalizedCategories;
    patch.primary_category = derivePrimaryCategory(normalizedCategories, form.primaryCategory);
  }
  if (form.applyNoteForm) patch.note_form = form.noteForm || NOTE_FORM_DEFAULT;
  if (form.applyLifecycleStage) patch.lifecycle_stage = form.lifecycleStage || NOTE_LIFECYCLE_STAGE_DEFAULT;
  if (form.applyPrivateLevel) patch.private_level = Math.max(0, Math.trunc(Number(form.privateLevel) || 0));
  if (form.applyWeight) {
    const normalizedWeight = normalizeNoteWeight(form.weight);
    if (form.weightMode === 'delta') patch.weight_delta = normalizedWeight;
    else patch.weight = normalizedWeight;
  }

  return patch;
};

const handleSubmit = async () => {
  if (props.noteIds.length === 0) {
    ElMessage.warning('请先选择节点');
    return;
  }

  const patch = buildPatch();
  if (Object.keys(patch).length === 0) {
    ElMessage.warning('请至少选择一项要批量修改的属性');
    return;
  }

  const result = await noteStore.batchUpdateNotes({
    ids: [...props.noteIds],
    patch,
  });
  if (!result) return;

  emit('saved', result);
  emit('update:modelValue', false);
};
</script>

<style scoped>
.batch-edit-dialog{display:flex;flex-direction:column;gap:16px}
.summary-line{font-size:13px;color:#606266}
.field-list{display:flex;flex-direction:column;gap:12px}
.field-row{display:flex;align-items:center;gap:12px}
.field-toggle{width:72px;flex:0 0 72px;color:#303133}
.field-control{flex:1;display:flex;align-items:center;min-width:0}
.compact-control{max-width:120px}
.weight-row{gap:8px}
.weight-mode-select{width:96px}
.number-input{width:96px}
.dialog-footer{display:flex;justify-content:flex-end;gap:8px}
</style>
