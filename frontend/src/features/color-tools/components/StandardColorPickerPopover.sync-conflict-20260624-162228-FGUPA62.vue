<template>
  <el-popover
    :visible="visible"
    trigger="click"
    :placement="placement"
    :width="width"
    :teleported="teleported"
    popper-class="standard-color-picker-popover"
    @update:visible="handleVisibleChange"
  >
    <template #reference>
      <slot name="reference" />
    </template>

    <ElColorPickerPanel
      :model-value="normalizedModelValue"
      color-format="hex"
      class="standard-color-picker-panel"
      @update:model-value="handlePanelValueChange"
    >
      <template #footer>
        <div class="picker-footer-stack">
          <div class="picker-value-strip">
            <div class="picker-value-pill">
              <span class="picker-value-label">HEX</span>
              <strong class="picker-value-text">{{ normalizedModelValue }}</strong>
            </div>
            <div class="picker-value-pill">
              <span class="picker-value-label">RGB</span>
              <strong class="picker-value-text">{{ rgbText }}</strong>
            </div>
          </div>

          <div class="picker-search-block">
            <div class="picker-search-bar">
              <el-input
                v-model="searchKeyword"
                size="small"
                clearable
                placeholder="按中英文名或 HEX 搜索，如 草莓 / Strawberry"
                @keydown.enter.prevent="applyFirstSearchResult"
              />
              <el-button
                size="small"
                plain
                :disabled="!searchResults.length"
                @click="applyFirstSearchResult"
              >
                匹配
              </el-button>
            </div>
            <div v-if="searchKeyword.trim() && searchResults.length" class="picker-search-results">
              <button
                v-for="color in searchResults"
                :key="color.hex"
                type="button"
                class="picker-search-item"
                :class="{ active: color.hex === normalizedModelValue }"
                @click="applySearchResult(color)"
              >
                <span class="picker-search-swatch" :style="{ backgroundColor: color.hex }" />
                <span class="picker-search-name">{{ color.displayName }}</span>
                <span class="picker-search-hex">{{ color.hex }}</span>
              </button>
            </div>
            <p v-else-if="searchKeyword.trim()" class="picker-search-hint">
              没有匹配的标准色，试试更完整的中文名、英文名或 HEX。
            </p>
          </div>

          <div class="picker-mapped-card" :title="mappedTooltip">
            <span
              class="mapped-color-swatch"
              :style="{ backgroundColor: mappedInfo.mappedColor.hex }"
            />
            <span class="picker-mapped-item picker-mapped-hex">{{ mappedInfo.mappedColor.hex }}</span>
            <span class="picker-mapped-item">距离 {{ mappedInfo.distance.toFixed(2) }}</span>
            <span class="picker-mapped-item picker-mapped-zh">{{ mappedZhText }}</span>
            <span v-if="mappedEnText" class="picker-mapped-item picker-mapped-en">{{ mappedEnText }}</span>
          </div>

          <div class="picker-footer-actions">
            <el-button size="small" text @click="resetDraft">重置</el-button>
            <el-button
              size="small"
              plain
              :disabled="mappedInfo.isExact"
              @click="applyMappedColor"
            >
              设为映射色
            </el-button>
          </div>
        </div>
      </template>
    </ElColorPickerPanel>
  </el-popover>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElColorPickerPanel, ElMessage } from 'element-plus'
import {
  fromHex,
  resolveMappedStandardColorInfo,
  searchStandardColors,
  toHex,
  type StandardColor,
} from '@/utils/colorToolkit'

const props = withDefaults(defineProps<{
  modelValue: string
  visible: boolean
  width?: number | string
  teleported?: boolean
  placement?: string
  resetValue?: string | null
}>(), {
  width: 340,
  teleported: false,
  placement: 'bottom',
  resetValue: null,
})

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'update:visible', value: boolean): void
}>()

const searchKeyword = ref('')
const sessionResetValue = ref('#606266')

const normalizePickerHex = (value: string | null | undefined, fallback = '#606266') => {
  try {
    return toHex(fromHex(value || fallback))
  } catch {
    return fallback
  }
}

const normalizedModelValue = computed(() => normalizePickerHex(props.modelValue))
const searchResults = computed(() => searchStandardColors(searchKeyword.value, 2))
const mappedInfo = computed(() => resolveMappedStandardColorInfo(normalizedModelValue.value, { range: 2, method: 'cie76' }))
const rgbText = computed(() => {
  const { r, g, b } = mappedInfo.value.sourceColor
  return `${r}, ${g}, ${b}`
})
const mappedZhText = computed(() => (
  mappedInfo.value.mappedColor.zhNames[0]
  || mappedInfo.value.mappedColor.displayName
))
const mappedEnText = computed(() => {
  const englishName = mappedInfo.value.mappedColor.enNames[0] || ''
  return englishName && englishName !== mappedZhText.value ? englishName : ''
})
const mappedTooltip = computed(() => {
  const labels = [mappedInfo.value.mappedColor.zhNames[0], mappedInfo.value.mappedColor.enNames[0]].filter(Boolean)
  return `当前颜色：${mappedInfo.value.sourceHex}；最接近标准色：${labels.join(' / ') || mappedInfo.value.mappedColor.displayName} · ${mappedInfo.value.mappedColor.hex}；距离 ${mappedInfo.value.distance.toFixed(2)}`
})

watch(() => props.visible, (visible) => {
  searchKeyword.value = ''
  if (visible) {
    sessionResetValue.value = normalizePickerHex(props.resetValue ?? props.modelValue)
  }
})

function handleVisibleChange(visible: boolean): void {
  emit('update:visible', visible)
}

function updateModelValue(value: string | null): void {
  if (!value) {
    emit('update:modelValue', sessionResetValue.value)
    return
  }

  emit('update:modelValue', normalizePickerHex(value, normalizedModelValue.value))
}

function handlePanelValueChange(value: string | null): void {
  updateModelValue(value)
}

function applySearchResult(color: StandardColor): void {
  searchKeyword.value = color.displayName
  emit('update:modelValue', color.hex)
}

function applyFirstSearchResult(): void {
  const firstMatch = searchResults.value[0]
  if (!firstMatch) {
    ElMessage.warning('没有匹配的标准色名')
    return
  }

  applySearchResult(firstMatch)
}

function applyMappedColor(): void {
  emit('update:modelValue', mappedInfo.value.mappedColor.hex)
}

function resetDraft(): void {
  emit('update:modelValue', sessionResetValue.value)
}
</script>

<style scoped>
.picker-footer-stack{display:flex;flex-direction:column;gap:10px;width:100%;min-width:0}
.picker-value-strip{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.picker-value-pill{display:flex;flex-direction:column;gap:3px;padding:7px 9px;border:1px solid #ebeef5;border-radius:8px;background:#f8fafc;min-width:0}
.picker-value-label{font-size:10px;line-height:1;color:#909399;letter-spacing:.04em}
.picker-value-text{display:block;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;line-height:1.35;color:#243046;font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,'Liberation Mono',monospace}
.picker-search-block{display:flex;flex-direction:column;gap:8px}
.picker-search-bar{display:flex;align-items:center;gap:8px}
.picker-search-bar :deep(.el-input){flex:1}
.picker-search-results{display:flex;flex-direction:column;gap:6px;max-height:148px;overflow:auto;padding-right:2px}
.picker-search-item{display:grid;grid-template-columns:14px minmax(0,1fr) auto;align-items:center;gap:8px;width:100%;padding:7px 8px;border:1px solid #ebeef5;border-radius:8px;background:#fff;color:#243046;cursor:pointer;text-align:left;transition:border-color .18s ease,background-color .18s ease}
.picker-search-item:hover{border-color:#c6d7ee;background:#f8fbff}
.picker-search-item.active{border-color:#409eff;background:#ecf5ff}
.picker-search-swatch{width:14px;height:14px;border-radius:999px;border:1px solid rgba(15,23,42,.12);box-shadow:inset 0 0 0 1px rgba(255,255,255,.35)}
.picker-search-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;line-height:1.3;font-weight:500}
.picker-search-hex{font-size:11px;line-height:1.2;color:#7a8799}
.picker-search-hint{margin:0;font-size:11px;line-height:1.4;color:#7a8799}
.picker-mapped-card{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:10px;border:1px solid #ebeef5;border-radius:10px;background:#f8fafc}
.picker-mapped-item{font-size:12px;line-height:1.3;color:#506078;white-space:nowrap}
.picker-mapped-hex,.picker-mapped-zh{color:#243046;font-weight:600}
.picker-mapped-en{color:#7a8799}
.mapped-color-swatch{flex:none;width:12px;height:12px;border-radius:999px;border:1px solid rgba(15,23,42,.12);box-shadow:inset 0 0 0 1px rgba(255,255,255,.35)}
.picker-footer-actions{display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap}
</style>

<style>
.standard-color-picker-popover.el-popover {
  max-width: calc(100vw - 40px);
  padding: 0 !important;
  border: none;
  box-shadow: 0 16px 40px rgba(15, 23, 42, 0.16);
  border-radius: 14px;
  overflow: hidden;
}

.standard-color-picker-popover .standard-color-picker-panel.el-color-picker-panel {
  width: 300px;
  max-width: 100%;
  padding: 12px;
}

.standard-color-picker-popover .el-color-picker-panel__wrapper {
  display: flex !important;
  align-items: stretch;
  gap: 8px;
  margin-bottom: 6px;
  overflow: visible;
}

.standard-color-picker-popover .el-color-svpanel {
  display: block !important;
  flex: none;
  order: 1;
  position: relative;
  width: 280px;
  height: 180px;
  background-image: linear-gradient(#0000, #000), linear-gradient(90deg, #fff, #fff0) !important;
}

.standard-color-picker-popover .el-color-hue-slider.is-vertical {
  display: block !important;
  flex: none;
  order: 2;
  box-sizing: border-box;
  position: relative;
  width: 12px;
  height: 180px;
  padding: 2px 0;
  float: none !important;
}

.standard-color-picker-popover .el-color-hue-slider.is-vertical .el-color-hue-slider__bar {
  height: 100%;
  background: linear-gradient(red 0%, #ff0 17%, #0f0 33%, #0ff 50%, #00f 67%, #f0f 83%, red 100%) !important;
}

.standard-color-picker-popover .el-color-hue-slider.is-vertical .el-color-hue-slider__thumb {
  box-sizing: border-box;
  position: absolute;
  left: 0;
  top: 0;
  width: 100%;
  height: 4px;
  border: 1px solid #ebeef5;
  border-radius: 1px;
  background: #fff;
  box-shadow: 0 0 2px rgba(0, 0, 0, 0.6);
}

.standard-color-picker-popover .el-color-picker-panel__footer {
  display: flex !important;
  flex-direction: column !important;
  align-items: stretch !important;
  gap: 10px;
  margin-top: 12px;
}

.standard-color-picker-popover .el-color-picker-panel__footer > .el-input {
  display: none !important;
}
</style>
