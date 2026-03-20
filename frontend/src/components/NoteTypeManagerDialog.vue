<template>
  <el-dialog
    :model-value="modelValue"
    title="管理分类"
    width="430px"
    top="6vh"
    class="note-type-manager-dialog"
    @update:model-value="value => emit('update:modelValue', value)"
  >
    <div class="manager-panel">
      <div class="manager-toolbar">
        <el-button size="small" type="primary" plain @click="addType">新增分类</el-button>
        <span class="manager-hint">拖拽调整顺序；名称不能重复。</span>
      </div>

      <div ref="managerListRef" class="manager-list">
        <div v-for="item in sortedDraftItems" :key="item.key" class="manager-row">
          <div class="row-line">
            <button
              type="button"
              class="drag-handle-btn"
              title="拖拽调整顺序"
              aria-label="拖拽调整顺序"
            >
              <el-icon><Rank /></el-icon>
            </button>
              <el-tag size="small" :type="hasUsage(item) ? 'danger' : 'info'" class="usage-tag">
                {{ hasUsage(item) ? `已绑定 ${getUsageDisplayCount(item)}` : '未绑定' }}
              </el-tag>
              <el-input
                v-model="item.label"
                size="small"
                placeholder="分类名称"
                class="label-input styled-label-input"
                :style="getLabelInputStyle(item)"
              />
            <el-color-picker
              v-model="item.color"
              color-format="hex"
              class="color-picker"
              @change="value => handleColorChange(item, value)"
            />
            <el-dropdown
              trigger="click"
              placement="bottom-end"
              :disabled="!hasUsage(item) || !getMergeTargetItems(item).length || Boolean(mergingKeys[item.key])"
              @command="targetKey => handleMerge(item, String(targetKey))"
            >
              <el-button
                size="small"
                text
                type="primary"
                class="icon-btn merge-btn"
                :loading="Boolean(mergingKeys[item.key])"
                title="合并到其他分类"
              >
                并
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item
                    v-for="target in getMergeTargetItems(item)"
                    :key="target.key"
                    :command="target.key"
                  >
                    合并到 {{ target.label }}
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
            <el-button
              size="small"
              text
              type="danger"
              class="icon-btn"
              @click="removeType(item)"
            >
              删
            </el-button>
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="dialog-footer">
        <el-button @click="emit('update:modelValue', false)">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue';
import Sortable from 'sortablejs';
import { Rank } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { mergeNoteCategoryPaletteItem } from '@/api/noteTypes';
import {
  createCustomNoteType,
  ensureNoteTypePaletteLoaded,
  getDefaultNodeTypeConfig,
  getEditableNoteTypePaletteItems,
  getNodeStyle,
  normalizeNodeColor,
  saveNoteTypePalette,
  type NoteTypePaletteItem
} from '@/utils/nodeConfig';

const props = defineProps<{
  modelValue: boolean;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void;
  (e: 'saved'): void;
}>();

const saving = ref(false);
const draftItems = ref<NoteTypePaletteItem[]>([]);
const managerListRef = ref<HTMLElement | null>(null);
const persistedKeys = ref<string[]>([]);
const mergingKeys = ref<Record<string, boolean>>({});
let sortable: Sortable | null = null;

const cloneItems = (items: NoteTypePaletteItem[]) => items.map(item => ({ ...item }));

const sortedDraftItems = computed(() => [...draftItems.value].sort((left, right) => {
  if (left.order !== right.order) return left.order - right.order;
  return left.label.localeCompare(right.label, 'zh-Hans-CN');
}));

const syncDraft = async () => {
  await ensureNoteTypePaletteLoaded();
  const items = getEditableNoteTypePaletteItems();
  draftItems.value = cloneItems(items);
  persistedKeys.value = items.map(item => item.key);
};

watch(() => props.modelValue, value => {
  if (!value) return;
  syncDraft().catch(error => {
    console.error(error);
    ElMessage.error('加载分类配置失败');
  });
}, { immediate: true });

watch(
  [() => props.modelValue, () => sortedDraftItems.value.length],
  async ([visible]) => {
    if (!visible) {
      destroySortable();
      return;
    }
    await nextTick();
    initSortable();
  },
  { immediate: true }
);

const addType = () => {
  draftItems.value.push(createCustomNoteType(`新分类${draftItems.value.length + 1}`));
};

const hasUsage = (item: NoteTypePaletteItem) => Number(item.usageCount ?? 0) > 0;

const getUsageDisplayCount = (item: NoteTypePaletteItem) => Math.ceil(Number(item.usageCount ?? 0));

const normalizeLabelKey = (value: string) => value.trim().toLocaleLowerCase('zh-Hans-CN');

const getMergeTargetItems = (item: NoteTypePaletteItem) => sortedDraftItems.value.filter(candidate => (
  candidate.key !== item.key
  && persistedKeys.value.includes(candidate.key)
));

const removeType = (item: NoteTypePaletteItem) => {
  if (!item.key) return;
  if (hasUsage(item)) {
    ElMessage.warning('该分类已被文档使用。可以先合并到其他分类，再删除。');
    return;
  }
  draftItems.value = draftItems.value.filter(entry => entry.key !== item.key);
};

const handleMerge = async (item: NoteTypePaletteItem, targetKey: string) => {
  const sourceKey = item.key;
  if (!sourceKey || !targetKey || sourceKey === targetKey || mergingKeys.value[sourceKey]) return;
  const target = sortedDraftItems.value.find(candidate => candidate.key === targetKey);
  if (!target) return;

  try {
    await ElMessageBox.confirm(
      `把“${item.label}”合并到“${target.label}”后，已有文档会改用目标分类和颜色。`,
      '合并分类',
      {
        confirmButtonText: '确认合并',
        cancelButtonText: '取消',
        type: 'warning',
      }
    );
  } catch (error) {
    if (error === 'cancel' || error === 'close') return;
    throw error;
  }

  mergingKeys.value = { ...mergingKeys.value, [sourceKey]: true };
  try {
    await mergeNoteCategoryPaletteItem(sourceKey, targetKey);
    await syncDraft();
    ElMessage.success('分类已合并');
    emit('saved');
  } catch (error) {
    console.error(error);
    ElMessage.error('合并分类失败');
  } finally {
    const { [sourceKey]: _removed, ...rest } = mergingKeys.value;
    mergingKeys.value = rest;
  }
};

const resequenceItems = (items: NoteTypePaletteItem[]) => {
  draftItems.value = items.map((item, index) => ({
    ...item,
    order: index * 10
  }));
};

const getLabelInputStyle = (item: NoteTypePaletteItem) => {
  const style = getNodeStyle(item.key, 'idea', item.color, null);
  return {
    '--type-input-border-color': style.borderColor,
    '--type-input-color': style.color,
    '--type-input-background': style.backgroundColor,
    '--type-input-border-width': style.borderWidth,
    '--type-input-border-style': style.borderStyle,
    '--type-input-opacity': style.opacity,
    '--type-input-text-decoration': style.textDecoration
  };
};

const handleColorChange = (item: NoteTypePaletteItem, value: string | null) => {
  item.color = normalizeNodeColor(value) ?? item.color;
};

const destroySortable = () => {
  if (!sortable) return;
  sortable.destroy();
  sortable = null;
};

const initSortable = () => {
  destroySortable();
  if (!managerListRef.value || sortedDraftItems.value.length < 2) return;
  sortable = Sortable.create(managerListRef.value, {
    handle: '.drag-handle-btn',
    animation: 150,
    ghostClass: 'type-manager-sortable-ghost',
    onEnd: ({ oldIndex, newIndex }) => {
      if (oldIndex == null || newIndex == null || oldIndex === newIndex) return;
      const ordered = [...sortedDraftItems.value];
      const [moved] = ordered.splice(oldIndex, 1);
      if (!moved) return;
      ordered.splice(newIndex, 0, moved);
      resequenceItems(ordered);
    },
  });
};

onUnmounted(() => destroySortable());

const handleSave = async () => {
  const normalized = draftItems.value
    .map((item, index) => ({
      ...item,
      label: item.label.trim() || item.key,
      color: normalizeNodeColor(item.color) ?? getDefaultNodeTypeConfig(item.key).baseColor,
      order: Number.isFinite(item.order) ? item.order : index * 10
    }));

  const seenLabels = new Set<string>();
  for (const item of normalized) {
    const normalizedLabel = normalizeLabelKey(item.label);
    if (!normalizedLabel) {
      ElMessage.warning('分类名称不能为空');
      return;
    }
    if (seenLabels.has(normalizedLabel)) {
      ElMessage.warning(`分类名称不能重复：${item.label}`);
      return;
    }
    seenLabels.add(normalizedLabel);
  }

  saving.value = true;
  try {
    await saveNoteTypePalette(normalized);
    ElMessage.success('分类配置已保存');
    emit('saved');
    emit('update:modelValue', false);
  } catch (error) {
    console.error(error);
    ElMessage.error('保存分类配置失败');
  } finally {
    saving.value = false;
  }
};
</script>

<style scoped>
.manager-panel{display:flex;flex-direction:column;gap:14px;height:100%;min-height:0}
.manager-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.manager-hint{font-size:12px;color:#909399}
.manager-list{display:flex;flex-direction:column;gap:10px;flex:1;min-height:0;overflow:auto;padding-right:4px}
.manager-row{border:1px solid #ebeef5;border-radius:10px;background:#fff;padding:10px 12px}
.row-line{display:grid;grid-template-columns:24px 72px minmax(0,160px) 40px 24px 24px;align-items:center;gap:8px;justify-content:start}
.drag-handle-btn{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;padding:0;border:none;background:transparent;color:#909399;cursor:move;border-radius:4px}
.drag-handle-btn:hover{background:rgba(64,158,255,.08);color:#409eff}
.usage-tag{justify-self:center;min-width:64px}
.label-input{min-width:0;width:160px;max-width:160px}
.styled-label-input{--type-input-border-color:#dcdfe6;--type-input-color:#606266;--type-input-background:#ffffff;--type-input-border-width:1px;--type-input-border-style:solid;--type-input-opacity:1;--type-input-text-decoration:none}
.styled-label-input :deep(.el-input__wrapper){box-shadow:none !important;border:var(--type-input-border-width) var(--type-input-border-style) var(--type-input-border-color);background:var(--type-input-background);border-radius:8px}
.styled-label-input :deep(.el-input__wrapper.is-focus){box-shadow:none !important;border-color:var(--type-input-border-color)}
.styled-label-input :deep(.el-input__inner){color:var(--type-input-color);text-align:center;font-weight:500;opacity:var(--type-input-opacity);text-decoration:var(--type-input-text-decoration)}
.color-picker{justify-self:center}
.merge-btn{justify-self:end}
.icon-btn{min-width:0;padding-left:4px;padding-right:4px;justify-self:end}
.type-manager-sortable-ghost{opacity:.7;background:#ecf5ff}
.dialog-footer{display:flex;justify-content:flex-end;gap:8px}
:deep(.note-type-manager-dialog.el-dialog){width:min(430px, calc(100vw - 24px)) !important;height:80vh;max-height:80vh;display:flex;flex-direction:column}
:deep(.note-type-manager-dialog .el-dialog__body){flex:1;min-height:0;overflow:hidden;padding-top:14px;padding-bottom:12px}
</style>
