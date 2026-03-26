<template>
  <el-dialog
    :model-value="modelValue"
    title="管理分类"
    width="740px"
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
        <div v-for="(item, index) in sortedDraftItems" :key="item.key" class="manager-row">
          <div class="row-line">
            <SortableOrderHandle
              :index="index"
              :total="sortedDraftItems.length"
              size="xs"
            />
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
            <StandardColorPickerPopover
              :model-value="getCommittedColorHex(item)"
              :visible="activeColorPickerKey === item.key"
              @update:model-value="value => handleColorPreview(item, value)"
              @update:visible="visible => handleColorPopoverVisibleChange(item, visible)"
            >
              <template #reference>
                <button
                  type="button"
                  class="color-picker-trigger"
                  :style="{ '--picker-color': getCommittedColorHex(item) }"
                  title="选择颜色并查看映射"
                  aria-label="选择颜色并查看映射"
                >
                  <span class="color-picker-trigger__swatch" />
                  <span class="color-picker-trigger__caret" />
                </button>
              </template>
            </StandardColorPickerPopover>
            <div class="mapped-name-chip" :title="getCommittedMappedColorTooltip(item)">
              <span
                class="mapped-color-swatch"
                :style="{ backgroundColor: getCommittedMappedColorInfo(item).mappedColor.hex }"
              />
              <span class="mapped-name-text">{{ getCommittedMappedColorPrimaryText(item) }}</span>
            </div>
            <div class="row-actions">
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
import { computed, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { mergeNoteCategoryPaletteItem } from '@/api/noteTypes';
import SortableOrderHandle from './SortableOrderHandle.vue';
import {
  StandardColorPickerPopover,
  resolveMappedStandardColorInfo,
  type ResolvedMappedColorInfo
} from '@/features/color-tools';
import { useSortableList } from '@/utils/useSortableList';
import { fromHex, getReadableTextColor } from '@/utils/colorToolkit';
import {
  createCustomNoteType,
  ensureNoteTypePaletteLoaded,
  getDefaultNodeTypeConfig,
  getEditableNoteTypePaletteItems,
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
const activeColorPickerKey = ref<string | null>(null);

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
  if (!value) {
    activeColorPickerKey.value = null;
    return;
  }
  syncDraft().catch(error => {
    console.error(error);
    ElMessage.error('加载分类配置失败');
  });
}, { immediate: true });

useSortableList({
  listRef: managerListRef,
  getDeps: () => [props.modelValue, sortedDraftItems.value.length] as const,
  isEnabled: () => props.modelValue && sortedDraftItems.value.length > 1,
  ghostClass: 'type-manager-sortable-ghost',
  onReorder: (oldIndex, newIndex) => {
    const ordered = [...sortedDraftItems.value];
    const [moved] = ordered.splice(oldIndex, 1);
    if (!moved) return;
    ordered.splice(newIndex, 0, moved);
    resequenceItems(ordered);
  },
});

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
  const backgroundColor = getCommittedColorHex(item);
  const foregroundColor = getReadableTextColor(fromHex(backgroundColor));
  const placeholderColor = foregroundColor === '#FFFFFF'
    ? 'rgba(255,255,255,.72)'
    : 'rgba(17,24,39,.58)';
  const focusRing = foregroundColor === '#FFFFFF'
    ? 'rgba(255,255,255,.18)'
    : 'rgba(17,24,39,.12)';
  return {
    '--type-input-border-color': backgroundColor,
    '--type-input-color': foregroundColor,
    '--type-input-background': backgroundColor,
    '--type-input-placeholder-color': placeholderColor,
    '--type-input-caret-color': foregroundColor,
    '--type-input-focus-ring': focusRing,
    '--type-input-border-width': '1px',
    '--type-input-border-style': 'solid',
    '--type-input-opacity': '1',
    '--type-input-text-decoration': 'none'
  };
};

const getDefaultColorHex = (item: NoteTypePaletteItem) => getDefaultNodeTypeConfig(item.key).baseColor;

const getCommittedColorHex = (item: NoteTypePaletteItem) => (
  normalizeNodeColor(item.color)
  ?? getDefaultColorHex(item)
);

const handleColorPreview = (item: NoteTypePaletteItem, value: string | null) => {
  item.color = normalizeNodeColor(value) ?? getDefaultColorHex(item);
};

const handleColorPopoverVisibleChange = (item: NoteTypePaletteItem, visible: boolean) => {
  if (visible) {
    activeColorPickerKey.value = item.key;
    return;
  }

  if (activeColorPickerKey.value === item.key) {
    activeColorPickerKey.value = null;
  }
};

const getMappedColorInfoByHex = (colorHex: string): ResolvedMappedColorInfo => (
  resolveMappedStandardColorInfo(colorHex, { range: 2, method: 'cie76' })
);

const getCommittedMappedColorInfo = (item: NoteTypePaletteItem): ResolvedMappedColorInfo => (
  getMappedColorInfoByHex(getCommittedColorHex(item))
);

const formatMappedColorPrimaryText = (info: ResolvedMappedColorInfo) => info.mappedColor.displayName;

const formatMappedColorTooltip = (info: ResolvedMappedColorInfo) => {
  const labels = [info.mappedColor.zhNames[0], info.mappedColor.enNames[0]].filter(Boolean);
  return `当前颜色：${info.sourceHex}；最接近标准色：${labels.join(' / ') || info.mappedColor.displayName} · ${info.mappedColor.hex}；距离 ${info.distance.toFixed(2)}`;
};

const getCommittedMappedColorPrimaryText = (item: NoteTypePaletteItem) => (
  formatMappedColorPrimaryText(getCommittedMappedColorInfo(item))
);

const getCommittedMappedColorTooltip = (item: NoteTypePaletteItem) => (
  formatMappedColorTooltip(getCommittedMappedColorInfo(item))
);

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
.manager-row{display:flex;flex-direction:column;border:1px solid #ebeef5;border-radius:10px;background:#fff;padding:10px 12px}
.row-line{display:grid;grid-template-columns:max-content 88px minmax(160px,220px) 40px minmax(132px,1fr) 72px;align-items:center;gap:10px;justify-content:start}
.usage-tag{justify-self:stretch;min-width:0;width:100%;padding:0 6px}
.label-input{min-width:0;width:100%;max-width:none}
.styled-label-input{--type-input-border-color:#dcdfe6;--type-input-color:#606266;--type-input-background:#ffffff;--type-input-placeholder-color:rgba(96,98,102,.56);--type-input-caret-color:#606266;--type-input-focus-ring:rgba(17,24,39,.08);--type-input-border-width:1px;--type-input-border-style:solid;--type-input-opacity:1;--type-input-text-decoration:none}
.styled-label-input :deep(.el-input__wrapper){box-shadow:none !important;border:var(--type-input-border-width) var(--type-input-border-style) var(--type-input-border-color);background:var(--type-input-background);border-radius:8px}
.styled-label-input :deep(.el-input__wrapper.is-focus){box-shadow:0 0 0 2px var(--type-input-focus-ring) !important;border-color:var(--type-input-border-color)}
.styled-label-input :deep(.el-input__inner){color:var(--type-input-color);text-align:left;font-weight:600;opacity:var(--type-input-opacity);text-decoration:var(--type-input-text-decoration);caret-color:var(--type-input-caret-color)}
.styled-label-input :deep(.el-input__inner::placeholder){color:var(--type-input-placeholder-color)}
.color-picker-trigger{position:relative;display:inline-flex;align-items:center;justify-content:center;width:40px;height:32px;padding:0;border:1px solid #dcdfe6;border-radius:8px;background:#fff;cursor:pointer;transition:border-color .18s ease,box-shadow .18s ease}
.color-picker-trigger:hover{border-color:#c0c4cc}
.color-picker-trigger:focus-visible{outline:none;border-color:#409eff;box-shadow:0 0 0 2px rgba(64,158,255,.16)}
.color-picker-trigger__swatch{width:18px;height:18px;border-radius:6px;background:var(--picker-color);border:1px solid rgba(15,23,42,.14);box-shadow:inset 0 0 0 1px rgba(255,255,255,.28)}
.color-picker-trigger__caret{position:absolute;right:6px;bottom:5px;width:0;height:0;border-left:4px solid transparent;border-right:4px solid transparent;border-top:5px solid rgba(36,48,70,.7)}
.mapped-color-swatch{flex:none;width:12px;height:12px;border-radius:999px;border:1px solid rgba(15,23,42,.12);box-shadow:inset 0 0 0 1px rgba(255,255,255,.35)}
.mapped-name-chip{display:flex;align-items:center;gap:6px;min-width:0;width:100%;height:32px;padding:0 8px;border:1px solid #ebeef5;border-radius:8px;background:#f8fafc}
.mapped-name-text{display:block;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;line-height:1.2;color:#243046;font-weight:500}
.row-actions{display:flex;align-items:center;justify-content:flex-end;gap:10px;min-width:0;padding-left:6px}
.merge-btn{justify-self:auto}
.icon-btn{min-width:0;padding-left:4px;padding-right:4px;justify-self:auto}
.type-manager-sortable-ghost{opacity:.7;background:#ecf5ff}
.dialog-footer{display:flex;justify-content:flex-end;gap:8px}
:deep(.note-type-manager-dialog.el-dialog){width:min(740px, calc(100vw - 24px)) !important;height:80vh;max-height:80vh;display:flex;flex-direction:column}
:deep(.note-type-manager-dialog .el-dialog__body){flex:1;min-height:0;overflow:hidden;padding-top:14px;padding-bottom:12px}

@media (max-width: 720px) {
  .manager-row{padding:10px}
  .row-line{grid-template-columns:18px 72px minmax(120px,1fr) 36px minmax(88px,1fr) 56px;gap:6px}
  .usage-tag{padding:0 4px}
  .mapped-name-chip{padding:0 6px}
  .mapped-name-chip .mapped-color-swatch{display:none}
  .row-actions{gap:6px;padding-left:2px}
}
</style>
