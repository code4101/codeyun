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

    <div class="standard-color-picker-surface">
      <div class="picker-current-row">
        <span
          class="picker-current-swatch"
          :style="{ backgroundColor: normalizedModelValue }"
        />
        <div class="picker-current-values">
          <strong>{{ normalizedModelValue }}</strong>
          <span>RGB {{ rgbText }}</span>
        </div>
      </div>

      <div class="picker-palette-grid" role="grid" aria-label="预设色板">
        <div
          v-for="(row, rowIndex) in quickPaletteRows"
          :key="rowIndex"
          class="picker-palette-row"
          role="row"
        >
          <button
            v-for="(swatch, columnIndex) in row"
            :key="`${rowIndex}-${columnIndex}-${swatch.hex}`"
            type="button"
            class="picker-swatch-button"
            :class="{ active: swatch.hex === normalizedModelValue }"
            :style="{ '--swatch-color': swatch.hex }"
            :title="formatSwatchTitle(swatch)"
            :aria-label="formatSwatchTitle(swatch)"
            @click="applyPaletteSwatch(swatch)"
          >
            <span class="picker-swatch-fill" />
          </button>
        </div>
      </div>

      <div class="picker-search-block">
        <div class="picker-search-bar">
          <el-input
            v-model="searchKeyword"
            size="small"
            clearable
            placeholder="名称 / HEX / RGB"
            @keydown.enter.prevent="applyFirstSearchResult"
          />
          <el-button
            size="small"
            plain
            :disabled="!canApplySearch"
            @click="applyFirstSearchResult"
          >
            匹配
          </el-button>
        </div>
        <div v-if="searchKeyword.trim() && canApplySearch" class="picker-search-results">
          <button
            v-if="parsedSearchHex"
            type="button"
            class="picker-search-item picker-search-item-direct"
            :class="{ active: parsedSearchHex === normalizedModelValue }"
            @click="applyParsedSearchColor"
          >
            <span class="picker-search-swatch" :style="{ backgroundColor: parsedSearchHex }" />
            <span class="picker-search-name">使用输入色</span>
            <span class="picker-search-hex">{{ parsedSearchHex }}</span>
          </button>
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
          没有匹配的颜色
        </p>
      </div>

      <button
        type="button"
        class="picker-mapped-row"
        :disabled="mappedInfo.isExact"
        :title="mappedTooltip"
        @click="applyMappedColor"
      >
        <span
          class="mapped-color-swatch"
          :style="{ backgroundColor: mappedInfo.mappedColor.hex }"
        />
        <span class="picker-mapped-hex">{{ mappedInfo.mappedColor.hex }}</span>
        <span class="picker-mapped-distance">距离 {{ mappedInfo.distance.toFixed(2) }}</span>
        <span class="picker-mapped-name">{{ mappedZhText }}</span>
        <span v-if="mappedEnText" class="picker-mapped-en">{{ mappedEnText }}</span>
      </button>

      <div class="picker-advanced-block">
        <button
          type="button"
          class="picker-advanced-toggle"
          :aria-expanded="customColorPanelOpen"
          @click="customColorPanelOpen = !customColorPanelOpen"
        >
          <span>自定义颜色</span>
          <span
            class="picker-advanced-chevron"
            :class="{ open: customColorPanelOpen }"
            aria-hidden="true"
          >
            ▾
          </span>
        </button>
        <ElColorPickerPanel
          v-if="customColorPanelOpen"
          :model-value="normalizedModelValue"
          color-format="hex"
          class="standard-color-picker-panel"
          @update:model-value="handlePanelValueChange"
        >
          <template #footer>
            <div class="picker-custom-color-footer" />
          </template>
        </ElColorPickerPanel>
      </div>

      <div class="picker-footer-actions">
        <el-button size="small" type="primary" text @click="confirmSelection">确认</el-button>
      </div>
    </div>
  </el-popover>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElColorPickerPanel, ElMessage } from 'element-plus'
import {
  createRgbColor,
  fromHex,
  parseColorInput,
  resolveMappedStandardColorInfo,
  searchStandardColors,
  toHex,
  type StandardColor,
} from '@/utils/colorToolkit'

interface PaletteSwatch {
  hex: string
  label: string
}

const props = withDefaults(defineProps<{
  modelValue: string
  visible: boolean
  width?: number | string
  teleported?: boolean
  placement?: string
  resetValue?: string | null
}>(), {
  width: 380,
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
const customColorPanelOpen = ref(false)

const createPaletteRow = (entries: Array<[string, string]>): PaletteSwatch[] => (
  entries.map(([hex, label]) => ({ hex, label }))
)

const quickPaletteRows: PaletteSwatch[][] = [
  createPaletteRow([
    ['#FFFFFF', '白色'],
    ['#000000', '黑色'],
    ['#E7E6E6', '浅灰'],
    ['#44546A', '蓝灰'],
    ['#5B9BD5', '蓝色'],
    ['#ED7D31', '橙色'],
    ['#A5A5A5', '中灰'],
    ['#FFC000', '金黄'],
    ['#4472C4', '深蓝'],
    ['#70AD47', '绿色'],
  ]),
  createPaletteRow([
    ['#F2F2F2', '白色 15% 深色'],
    ['#7F7F7F', '黑色 50% 浅色'],
    ['#D0CECE', '浅灰 15% 深色'],
    ['#D6DCE4', '蓝灰 80% 浅色'],
    ['#DDEBF7', '蓝色 80% 浅色'],
    ['#FCE4D6', '橙色 80% 浅色'],
    ['#EDEDED', '中灰 80% 浅色'],
    ['#FFF2CC', '金黄 80% 浅色'],
    ['#D9E2F3', '深蓝 80% 浅色'],
    ['#E2F0D9', '绿色 80% 浅色'],
  ]),
  createPaletteRow([
    ['#D9D9D9', '白色 25% 深色'],
    ['#595959', '黑色 35% 浅色'],
    ['#AEAAAA', '浅灰 25% 深色'],
    ['#ADB9CA', '蓝灰 60% 浅色'],
    ['#BDD7EE', '淡蓝'],
    ['#F8CBAD', '淡橙'],
    ['#DBDBDB', '中灰 60% 浅色'],
    ['#FFE699', '淡黄'],
    ['#B4C6E7', '淡钴蓝'],
    ['#C6E0B4', '淡草绿'],
  ]),
  createPaletteRow([
    ['#BFBFBF', '白色 35% 深色'],
    ['#3F3F3F', '黑色 25% 浅色'],
    ['#757171', '浅灰 50% 深色'],
    ['#8497B0', '蓝灰 40% 浅色'],
    ['#9DC3E6', '湖蓝'],
    ['#F4B183', '杏橙'],
    ['#C9C9C9', '中灰 40% 浅色'],
    ['#FFD966', '明黄'],
    ['#8EA9DB', '蓝紫'],
    ['#A9D18E', '草绿'],
  ]),
  createPaletteRow([
    ['#A6A6A6', '白色 50% 深色'],
    ['#262626', '黑色 15% 浅色'],
    ['#3A3838', '浅灰 75% 深色'],
    ['#323E4F', '蓝灰 25% 深色'],
    ['#2E75B5', '蓝色 25% 深色'],
    ['#C55A11', '橙色 25% 深色'],
    ['#7B7B7B', '中灰 25% 深色'],
    ['#BF9000', '金黄 25% 深色'],
    ['#2F5597', '深蓝 25% 深色'],
    ['#548235', '绿色 25% 深色'],
  ]),
  createPaletteRow([
    ['#7F7F7F', '白色 65% 深色'],
    ['#0C0C0C', '黑色 5% 浅色'],
    ['#171717', '浅灰 90% 深色'],
    ['#222A35', '蓝灰 50% 深色'],
    ['#1F4E79', '深海蓝'],
    ['#843C0C', '棕橙'],
    ['#525252', '深银灰'],
    ['#806000', '棕黄'],
    ['#203864', '藏蓝'],
    ['#375623', '深绿'],
  ]),
]

const normalizePickerHex = (value: string | null | undefined, fallback = '#606266') => {
  try {
    return toHex(fromHex(value || fallback))
  } catch {
    return fallback
  }
}

const normalizedModelValue = computed(() => normalizePickerHex(props.modelValue))
const parsedSearchHex = computed(() => parseQuickColorInput(searchKeyword.value))
const searchResults = computed(() => (
  searchStandardColors(searchKeyword.value, 2, 8)
    .filter(color => color.hex !== parsedSearchHex.value)
))
const canApplySearch = computed(() => Boolean(parsedSearchHex.value || searchResults.value.length))
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
    customColorPanelOpen.value = false
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

function parseQuickColorInput(input: string): string | null {
  const text = input.trim()
  if (!text) return null

  const bareRgbMatch = text.match(/^(\d{1,3})\s*[,，\s]\s*(\d{1,3})\s*[,，\s]\s*(\d{1,3})$/)
  if (bareRgbMatch) {
    const channels = bareRgbMatch.slice(1).map(value => Number.parseInt(value, 10))
    if (channels.every(value => value >= 0 && value <= 255)) {
      return toHex(createRgbColor(channels[0], channels[1], channels[2]))
    }
    return null
  }

  const parsed = parseColorInput(text, 2)
  return parsed ? toHex(parsed) : null
}

function formatSwatchTitle(swatch: PaletteSwatch): string {
  return `${swatch.label} ${swatch.hex}`
}

function applyPaletteSwatch(swatch: PaletteSwatch): void {
  emit('update:modelValue', swatch.hex)
}

function applySearchResult(color: StandardColor): void {
  searchKeyword.value = color.displayName
  emit('update:modelValue', color.hex)
}

function applyParsedSearchColor(): void {
  if (!parsedSearchHex.value) {
    return
  }

  emit('update:modelValue', parsedSearchHex.value)
}

function applyFirstSearchResult(): void {
  if (parsedSearchHex.value) {
    applyParsedSearchColor()
    return
  }

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

function confirmSelection(): void {
  emit('update:visible', false)
}
</script>

<style scoped>
.standard-color-picker-surface{box-sizing:border-box;display:flex;flex-direction:column;gap:10px;width:100%;min-width:0;max-height:calc(100vh - 32px);overflow:auto;padding:12px}
.picker-current-row{display:grid;grid-template-columns:28px minmax(0,1fr);align-items:center;gap:10px}
.picker-current-swatch{width:28px;height:28px;border-radius:7px;border:1px solid rgba(15,23,42,.12);box-shadow:inset 0 0 0 1px rgba(255,255,255,.35)}
.picker-current-values{display:flex;align-items:baseline;gap:10px;min-width:0}
.picker-current-values strong{font-size:13px;line-height:1.3;color:#243046;font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,'Liberation Mono',monospace}
.picker-current-values span{font-size:12px;line-height:1.3;color:#64748b;font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,'Liberation Mono',monospace}
.picker-palette-grid{display:flex;flex-direction:column;gap:7px}
.picker-palette-row{display:grid;grid-template-columns:repeat(10,28px);gap:7px}
.picker-swatch-button{box-sizing:border-box;width:28px;height:28px;padding:0;border:1px solid #d8dee8;border-radius:6px;background:#fff;cursor:pointer;transition:border-color .15s ease,box-shadow .15s ease,transform .15s ease}
.picker-swatch-button:hover{border-color:#8fb7ee;box-shadow:0 4px 10px rgba(15,23,42,.12);transform:translateY(-1px)}
.picker-swatch-button.active{border-color:#2563eb;box-shadow:0 0 0 2px rgba(37,99,235,.18)}
.picker-swatch-fill{display:block;width:100%;height:100%;border-radius:5px;background:var(--swatch-color);box-shadow:inset 0 0 0 1px rgba(255,255,255,.35)}
.picker-search-block{display:flex;flex-direction:column;gap:8px}
.picker-search-bar{display:flex;align-items:center;gap:8px;min-width:0}
.picker-search-bar :deep(.el-input){flex:1}
.picker-search-bar :deep(.el-button){flex:none}
.picker-search-results{display:flex;flex-direction:column;gap:6px;max-height:150px;overflow:auto;padding-right:2px}
.picker-search-item{display:grid;grid-template-columns:14px minmax(0,1fr) auto;align-items:center;gap:8px;width:100%;padding:7px 8px;border:1px solid #ebeef5;border-radius:8px;background:#fff;color:#243046;cursor:pointer;text-align:left;transition:border-color .18s ease,background-color .18s ease}
.picker-search-item:hover{border-color:#c6d7ee;background:#f8fbff}
.picker-search-item.active{border-color:#409eff;background:#ecf5ff}
.picker-search-item-direct{border-color:#dbeafe;background:#f8fbff}
.picker-search-swatch{width:14px;height:14px;border-radius:999px;border:1px solid rgba(15,23,42,.12);box-shadow:inset 0 0 0 1px rgba(255,255,255,.35)}
.picker-search-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;line-height:1.3;font-weight:500}
.picker-search-hex{font-size:11px;line-height:1.2;color:#7a8799}
.picker-search-hint{margin:0;font-size:11px;line-height:1.4;color:#7a8799}
.picker-mapped-row{box-sizing:border-box;display:flex;align-items:center;gap:8px;flex-wrap:wrap;width:100%;padding:9px 10px;border:1px solid #ebeef5;border-radius:8px;background:#f8fafc;color:#506078;cursor:pointer;text-align:left;transition:border-color .18s ease,background-color .18s ease}
.picker-mapped-row:not(:disabled):hover{border-color:#c6d7ee;background:#f8fbff}
.picker-mapped-row:disabled{cursor:default}
.picker-mapped-row span{font-size:12px;line-height:1.3;white-space:nowrap}
.picker-mapped-hex,.picker-mapped-name{color:#243046;font-weight:600}
.picker-mapped-distance{color:#64748b}
.picker-mapped-en{color:#7a8799}
.mapped-color-swatch{flex:none;width:12px;height:12px;border-radius:999px;border:1px solid rgba(15,23,42,.12);box-shadow:inset 0 0 0 1px rgba(255,255,255,.35)}
.picker-advanced-block{display:flex;flex-direction:column;gap:8px}
.picker-advanced-toggle{display:flex;align-items:center;justify-content:space-between;width:100%;padding:7px 0;border:none;background:transparent;color:#526173;font-size:12px;line-height:1.3;cursor:pointer}
.picker-advanced-toggle:hover{color:#1d4ed8}
.picker-advanced-chevron{font-size:11px;line-height:1;transition:transform .15s ease}
.picker-advanced-chevron.open{transform:rotate(180deg)}
.picker-custom-color-footer{display:none}
.picker-footer-actions{display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap}
</style>

<style>
.standard-color-picker-popover.el-popover {
  max-width: calc(100vw - 40px);
  padding: 0 !important;
  border: none;
  box-shadow: 0 16px 40px rgba(15, 23, 42, 0.16);
  border-radius: 10px;
  overflow: hidden;
}

.standard-color-picker-popover .standard-color-picker-panel.el-color-picker-panel {
  width: 100%;
  max-width: 100%;
  padding: 0;
  border: none;
  box-shadow: none;
}

.standard-color-picker-popover .el-color-picker-panel__wrapper {
  display: flex !important;
  align-items: stretch;
  gap: 8px;
  margin-bottom: 0;
  overflow: visible;
}

.standard-color-picker-popover .el-color-svpanel {
  display: block !important;
  flex: none;
  order: 1;
  position: relative;
  width: 326px;
  height: 154px;
  background-image: linear-gradient(#0000, #000), linear-gradient(90deg, #fff, #fff0) !important;
}

.standard-color-picker-popover .el-color-hue-slider.is-vertical {
  display: block !important;
  flex: none;
  order: 2;
  box-sizing: border-box;
  position: relative;
  width: 12px;
  height: 154px;
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
  gap: 0;
  margin-top: 0;
}

.standard-color-picker-popover .el-color-picker-panel__footer > .el-input {
  display: none !important;
}

.standard-color-picker-popover .el-color-picker-panel__footer {
  min-height: 0;
  padding: 0;
}
</style>
