<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Close, CopyDocument, Download, Picture, Plus, UploadFilled } from '@element-plus/icons-vue'
import {
  analyzeImageColorDistribution,
  getTopCoveragePercent,
  COLOR_GROUPS,
  DISTANCE_METHODS,
  StandardColorPickerPopover,
  createRgbColor,
  fromHex,
  fromVbaValue,
  getColorDistance,
  getGroupPalette,
  getPaletteForGroups,
  getReadableTextColor,
  getStandardColors,
  matchesStandardColorKeyword,
  mixWeightedColors,
  resolveMappedStandardColorInfo,
  toHex,
  toPercentage,
  toVbaValue,
  type AnalysisProgress,
  type ImageColorDistributionResult,
  type ColorGroupId,
  type DistanceMethod,
  type RgbColor,
  type ResolvedMappedColorInfo,
  type StandardColor,
} from '@/features/color-tools'

interface MixComposerEntry {
  id: string
  hex: string
  weight: number
}

interface ResolvedMixComposerEntry extends MixComposerEntry {
  effectiveHex: string
  mappedInfo: ResolvedMappedColorInfo
  secondaryName: string
}

interface PersistedColorToolsState {
  version: 1
  currentHex: string
  mixFillHex: string
  mixEntries: Array<{
    hex: string
    weight: number
  }>
}

const DEFAULT_CURRENT_HEX = '#87CEEB'
const LEGACY_DEFAULT_CURRENT_HEX = '#7B2D54'
const DEFAULT_MIX_FILL_HEX = DEFAULT_CURRENT_HEX
const LEGACY_DEFAULT_MIX_FILL_HEX = '#FFFFFF'
const DEFAULT_CURRENT_COLOR = fromHex(DEFAULT_CURRENT_HEX)

const currentColor = reactive(
  createRgbColor(DEFAULT_CURRENT_COLOR.r, DEFAULT_CURRENT_COLOR.g, DEFAULT_CURRENT_COLOR.b),
)
const activePalette = ref<ColorGroupId>('core-zh')
const paletteSearch = ref('')
const hexDraft = ref(DEFAULT_CURRENT_HEX)
const vbaPositiveDraft = ref('')
const vbaNegativeDraft = ref('')
const VBA_COLOR_SPACE = 256 ** 3
const COLOR_TOOLS_STATE_STORAGE_KEY = 'color_tools_state_v1'
let mixComposerEntrySeed = 0
const createMixComposerEntry = (hex = DEFAULT_MIX_FILL_HEX, weight = 100): MixComposerEntry => ({
  id: `mix-${++mixComposerEntrySeed}`,
  hex: toHex(fromHex(hex)),
  weight,
})
const mixEntries = ref<MixComposerEntry[]>([createMixComposerEntry(DEFAULT_CURRENT_HEX, 100)])
const activeMixEntryPickerId = ref<string | null>(null)
const mixFillHex = ref(DEFAULT_MIX_FILL_HEX)
const mixFillPickerVisible = ref(false)

const analysisGroupIds = ref<ColorGroupId[]>(COLOR_GROUPS.map(group => group.id))
const analysisDistanceMethod = ref<DistanceMethod>('cie76')
const imageInputRef = ref<HTMLInputElement | null>(null)
const sourceImageBlob = ref<Blob | null>(null)
const sourceImageName = ref('')
const sourceImageUrl = ref('')
const projectedImageUrl = ref('')
const isAnalyzingImage = ref(false)
const analysisProgress = reactive<AnalysisProgress>({
  stage: 'counting',
  current: 0,
  total: 1,
  ratio: 0,
  message: '等待载入图片',
})
const analysisResult = ref<ImageColorDistributionResult | null>(null)
let analysisRunId = 0

const groupPalettes: Record<ColorGroupId, StandardColor[]> = {
  'core-zh': getGroupPalette('core-zh'),
  'extended-zh': getGroupPalette('extended-zh'),
  'english-expanded': getGroupPalette('english-expanded'),
}

const currentHex = computed(() => toHex(currentColor))
const currentPickerVisible = ref(false)

watch(
  () => [currentColor.r, currentColor.g, currentColor.b],
  () => {
    hexDraft.value = currentHex.value
    vbaPositiveDraft.value = String(toVbaValue(currentColor))
    vbaNegativeDraft.value = String(toVbaValue(currentColor, true))
  },
  { immediate: true },
)

watch(
  [currentHex, mixFillHex, mixEntries],
  () => {
    persistColorToolsState()
  },
  { deep: true },
)

const percentageValues = computed(() => toPercentage(currentColor))
const percentagePercentLabel = computed(() => percentageValues.value.map(value => `${(value * 100).toFixed(2)}%`).join(' / '))

const similarityRows = computed(() => {
  const sourceHex = currentHex.value
  const sourceColor = currentColor

  return getStandardColors(2)
    .map((color) => {
      const primaryEnglishName = color.enNames[0] || ''
      const secondaryName = primaryEnglishName && primaryEnglishName !== color.displayName
        ? primaryEnglishName
        : ''
      const distance = color.hex === sourceHex ? 0 : getColorDistance(sourceColor, color, 'cie76')

      return {
        color,
        secondaryName,
        distance,
      }
    })
    .sort((left, right) => {
      if (left.distance !== right.distance) return left.distance - right.distance
      if (left.color.displayName.length !== right.color.displayName.length) {
        return left.color.displayName.length - right.color.displayName.length
      }
      return left.color.hex.localeCompare(right.color.hex)
    })
    .slice(0, 5)
    .map(row => ({
      ...row,
      delta: row.distance.toFixed(2),
    }))
})

function filterPaletteByKeyword(colors: StandardColor[], keyword: string): StandardColor[] {
  if (!keyword.trim()) {
    return colors
  }

  return colors.filter(color => matchesStandardColorKeyword(color, keyword))
}

const filteredPalette = computed(() => {
  const colors = groupPalettes[activePalette.value]
  return filterPaletteByKeyword(colors, paletteSearch.value)
})

const normalizeMixWeight = (value: unknown) => {
  const numeric = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(numeric)) return 0
  return Math.max(0, Math.min(100, Math.round(numeric)))
}

function createDefaultPersistedColorToolsState(): PersistedColorToolsState {
  return {
    version: 1,
    currentHex: DEFAULT_CURRENT_HEX,
    mixFillHex: DEFAULT_MIX_FILL_HEX,
    mixEntries: [{
      hex: DEFAULT_CURRENT_HEX,
      weight: 100,
    }],
  }
}

function isLegacyDefaultPersistedState(state: Partial<PersistedColorToolsState>): boolean {
  const sourceEntries = Array.isArray(state.mixEntries) ? state.mixEntries : []
  if (sourceEntries.length !== 1) return false

  try {
    const normalizedCurrentHex = toHex(fromHex(state.currentHex || LEGACY_DEFAULT_CURRENT_HEX))
    const normalizedMixFillHex = toHex(fromHex(state.mixFillHex || LEGACY_DEFAULT_MIX_FILL_HEX))
    const normalizedEntryHex = toHex(fromHex(sourceEntries[0]?.hex || ''))

    return (
      (normalizedCurrentHex === LEGACY_DEFAULT_CURRENT_HEX || normalizedCurrentHex === DEFAULT_CURRENT_HEX)
      && normalizedMixFillHex === LEGACY_DEFAULT_MIX_FILL_HEX
      && normalizedEntryHex === normalizedCurrentHex
      && normalizeMixWeight(sourceEntries[0]?.weight) === 100
    )
  } catch {
    return false
  }
}

const resolvedMixEntries = computed<ResolvedMixComposerEntry[]>(() => (
  mixEntries.value.map((entry) => {
    const effectiveHex = entry.hex
    const mappedInfo = resolveMappedStandardColorInfo(effectiveHex, { range: 2, method: 'cie76' })

    return {
      ...entry,
      effectiveHex,
      mappedInfo,
      secondaryName: getPaletteSecondaryName(mappedInfo.mappedColor),
    }
  })
))

const mixTotalWeight = computed(() => resolvedMixEntries.value.reduce((sum, entry) => sum + entry.weight, 0))
const mixFillWeight = computed(() => Math.max(100 - mixTotalWeight.value, 0))
const mixFillMappedInfo = computed(() => resolveMappedStandardColorInfo(mixFillHex.value, { range: 2, method: 'cie76' }))
const mixFillSecondaryName = computed(() => getPaletteSecondaryName(mixFillMappedInfo.value.mappedColor))
const mixedPreview = computed(() => (
  mixWeightedColors(
    resolvedMixEntries.value.map(entry => ({
      color: entry.effectiveHex,
      weight: entry.weight,
    })),
    {
      fillColor: mixFillHex.value,
      fillToWeight: 100,
    }
  )
  ?? fromHex(mixFillHex.value)
))
const mixedPreviewHex = computed(() => toHex(mixedPreview.value))
const mixedPreviewMappedInfo = computed(() => resolveMappedStandardColorInfo(mixedPreview.value, { range: 2, method: 'cie76' }))
const mixedPreviewPrimaryText = computed(() => mixedPreviewMappedInfo.value.mappedColor.displayName)
const mixedPreviewSecondaryName = computed(() => getPaletteSecondaryName(mixedPreviewMappedInfo.value.mappedColor))
const mixedPreviewTooltip = computed(() => {
  const color = mixedPreviewMappedInfo.value.mappedColor
  const labels = [color.zhNames[0], color.enNames[0]].filter(Boolean)
  return `当前混色：${mixedPreviewHex.value}；最接近标准色：${labels.join(' / ') || color.displayName} · ${color.hex}`
})

const analysisPaletteSize = computed(() => getPaletteForGroups(analysisGroupIds.value).length)
const analysisProgressPercent = computed(() => Math.max(0, Math.min(100, Math.round(analysisProgress.ratio * 100))))
const distributionRows = computed(() => (analysisResult.value?.rows ?? []).map(row => ({
  ...row,
  zhLabel: row.color.zhNames.join(' / ') || '—',
  enLabel: row.color.enNames.join(' / ') || '—',
})))
const topCoverageSummary = computed(() => {
  const rows = analysisResult.value?.rows ?? []
  return {
    top5: getTopCoveragePercent(rows, 5),
    top10: getTopCoveragePercent(rows, 10),
  }
})

function setCurrentColor(color: RgbColor): void {
  currentColor.r = color.r
  currentColor.g = color.g
  currentColor.b = color.b
}

function getSwatchStyle(color: RgbColor) {
  return {
    background: toHex(color),
    color: getReadableTextColor(color),
  }
}

function getContrastCardStyle(color: StandardColor | RgbColor | string): Record<string, string> {
  const normalizedColor = typeof color === 'string'
    ? fromHex(color)
    : 'hex' in color
      ? createRgbColor(color.r, color.g, color.b)
      : color
  const background = typeof color === 'string'
    ? toHex(normalizedColor)
    : 'hex' in color
      ? color.hex
      : toHex(color)
  return {
    background,
    color: getReadableTextColor(normalizedColor),
    ...getContrastVarsStyle(color),
  }
}

function getContrastVarsStyle(color: StandardColor | RgbColor | string): Record<string, string> {
  const normalizedColor = typeof color === 'string'
    ? fromHex(color)
    : 'hex' in color
      ? createRgbColor(color.r, color.g, color.b)
      : color
  const foreground = getReadableTextColor(normalizedColor)
  const lightForeground = foreground === '#111827'

  return {
    '--sim-fg': foreground,
    '--sim-fg-muted': lightForeground ? 'rgba(17,24,39,0.72)' : 'rgba(255,255,255,0.84)',
    '--sim-chip-bg': lightForeground ? 'rgba(255,255,255,0.56)' : 'rgba(15,23,42,0.22)',
    '--sim-chip-border': lightForeground ? 'rgba(17,24,39,0.24)' : 'rgba(255,255,255,0.46)',
  }
}

function getSimilarityCardStyle(color: StandardColor): Record<string, string> {
  return getContrastCardStyle(color)
}

function getHexCardStyle(color: RgbColor | string): Record<string, string> {
  return getContrastCardStyle(color)
}

function getPaletteSecondaryName(color: StandardColor): string {
  const primaryEnglishName = color.enNames[0] || ''
  return primaryEnglishName && primaryEnglishName !== color.displayName ? primaryEnglishName : ''
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(2)}%`
}

function formatInteger(value: number): string {
  return new Intl.NumberFormat('zh-CN').format(value)
}

function canUseLocalStorage(): boolean {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'
}

function buildPersistedColorToolsState(): PersistedColorToolsState {
  return {
    version: 1,
    currentHex: currentHex.value,
    mixFillHex: mixFillHex.value,
    mixEntries: mixEntries.value.map(entry => ({
      hex: entry.hex,
      weight: normalizeMixWeight(entry.weight),
    })),
  }
}

function persistColorToolsState(): void {
  if (!canUseLocalStorage()) return

  try {
    window.localStorage.setItem(
      COLOR_TOOLS_STATE_STORAGE_KEY,
      JSON.stringify(buildPersistedColorToolsState()),
    )
  } catch (error) {
    console.warn('Failed to persist color tools state:', error)
  }
}

function loadPersistedColorToolsState(): PersistedColorToolsState | null {
  if (!canUseLocalStorage()) return null

  try {
    const raw = window.localStorage.getItem(COLOR_TOOLS_STATE_STORAGE_KEY)
    if (!raw) return null

    const parsed = JSON.parse(raw) as Partial<PersistedColorToolsState> | null
    if (!parsed || parsed.version !== 1) {
      window.localStorage.removeItem(COLOR_TOOLS_STATE_STORAGE_KEY)
      return null
    }

    if (isLegacyDefaultPersistedState(parsed)) {
      window.localStorage.removeItem(COLOR_TOOLS_STATE_STORAGE_KEY)
      return createDefaultPersistedColorToolsState()
    }

    const currentHex = toHex(fromHex(parsed.currentHex || DEFAULT_CURRENT_HEX))
    const mixFillHex = toHex(fromHex(parsed.mixFillHex || DEFAULT_MIX_FILL_HEX))
    const sourceEntries = Array.isArray(parsed.mixEntries) ? parsed.mixEntries : []
    const mixEntries = sourceEntries
      .map((entry) => {
        try {
          return {
            hex: toHex(fromHex(entry?.hex || DEFAULT_MIX_FILL_HEX)),
            weight: normalizeMixWeight(entry?.weight),
          }
        } catch {
          return null
        }
      })
      .filter((entry): entry is PersistedColorToolsState['mixEntries'][number] => Boolean(entry))

    return {
      version: 1,
      currentHex,
      mixFillHex,
      mixEntries: sourceEntries.length === 0 ? [] : mixEntries,
    }
  } catch (error) {
    console.warn('Failed to load persisted color tools state:', error)
    window.localStorage.removeItem(COLOR_TOOLS_STATE_STORAGE_KEY)
    return null
  }
}

function restorePersistedColorToolsState(): void {
  const persisted = loadPersistedColorToolsState()
  if (!persisted) return

  setCurrentColor(fromHex(persisted.currentHex))
  mixFillHex.value = persisted.mixFillHex
  mixEntries.value = persisted.mixEntries.map(entry => createMixComposerEntry(entry.hex, entry.weight))
}

function applyHexDraft(): void {
  try {
    setCurrentColor(fromHex(hexDraft.value))
  } catch (error) {
    hexDraft.value = currentHex.value
    ElMessage.error(error instanceof Error ? error.message : 'HEX 颜色格式错误')
  }
}

function applyVbaDraft(negative = false): void {
  const draftRef = negative ? vbaNegativeDraft : vbaPositiveDraft
  const rawValue = draftRef.value.trim()

  if (!/^-?\d+$/.test(rawValue)) {
    draftRef.value = String(toVbaValue(currentColor, negative))
    ElMessage.error('VBA 值需填写整数')
    return
  }

  const parsed = Number.parseInt(rawValue, 10)
  const isPositiveRangeValid = parsed >= 0 && parsed < VBA_COLOR_SPACE
  const isNegativeRangeValid = parsed >= -VBA_COLOR_SPACE && parsed < 0

  if ((!negative && !isPositiveRangeValid) || (negative && !isNegativeRangeValid)) {
    draftRef.value = String(toVbaValue(currentColor, negative))
    ElMessage.error(
      negative
        ? `VBA 负值范围应为 ${-VBA_COLOR_SPACE} 到 -1`
        : `VBA 正值范围应为 0 到 ${VBA_COLOR_SPACE - 1}`,
    )
    return
  }

  try {
    setCurrentColor(fromVbaValue(parsed))
  } catch (error) {
    draftRef.value = String(toVbaValue(currentColor, negative))
    ElMessage.error(error instanceof Error ? error.message : 'VBA 数值格式错误')
  }
}

function selectPaletteColor(color: StandardColor): void {
  setCurrentColor(color)
}

function handleCurrentPickerPreview(value: string): void {
  try {
    const normalized = toHex(fromHex(value))
    setCurrentColor(fromHex(normalized))
  } catch {
    ElMessage.error('颜色选择器返回了无效颜色')
  }
}

function handleCurrentPickerVisibleChange(visible: boolean): void {
  currentPickerVisible.value = visible
}

function handleMixEntryColorPreview(entry: MixComposerEntry, value: string): void {
  entry.hex = toHex(fromHex(value))
}

function handleMixEntryPickerVisibleChange(entry: MixComposerEntry, visible: boolean): void {
  if (visible) {
    activeMixEntryPickerId.value = entry.id
    return
  }

  if (activeMixEntryPickerId.value === entry.id) {
    activeMixEntryPickerId.value = null
  }
}

function addMixEntry(): void {
  mixEntries.value.push(createMixComposerEntry(currentHex.value, 100))
}

function updateMixEntryWeight(entryId: string, value: unknown): void {
  mixEntries.value = mixEntries.value.map(entry => (
    entry.id === entryId
      ? { ...entry, weight: normalizeMixWeight(value) }
      : entry
  ))
}

function removeMixEntry(entryId: string): void {
  mixEntries.value = mixEntries.value.filter(entry => entry.id !== entryId)
  if (activeMixEntryPickerId.value === entryId) {
    activeMixEntryPickerId.value = null
  }
}

function handleMixFillColorPreview(value: string): void {
  mixFillHex.value = toHex(fromHex(value))
}

function handleMixFillPickerVisibleChange(visible: boolean): void {
  mixFillPickerVisible.value = visible
}

async function copyValue(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value)
      ElMessage.success('复制成功')
      return
    } catch (error) {
      console.warn('Clipboard API failed, falling back to execCommand', error)
    }
  }

  try {
    const textarea = document.createElement('textarea')
    textarea.value = value
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()

    const ok = document.execCommand('copy')
    document.body.removeChild(textarea)

    if (!ok) {
      throw new Error('copy failed')
    }

    ElMessage.success('复制成功')
  } catch (error) {
    console.error('Copy failed:', error)
    ElMessage.error('复制失败，请手动复制')
  }
}

function revokeObjectUrl(url: string): void {
  if (url) {
    URL.revokeObjectURL(url)
  }
}

function resetAnalysisUrls(): void {
  revokeObjectUrl(sourceImageUrl.value)
  revokeObjectUrl(projectedImageUrl.value)
  sourceImageUrl.value = ''
  projectedImageUrl.value = ''
}

function updateSourceImage(blob: Blob, name: string): void {
  analysisRunId += 1
  revokeObjectUrl(sourceImageUrl.value)
  revokeObjectUrl(projectedImageUrl.value)
  projectedImageUrl.value = ''
  sourceImageBlob.value = blob
  sourceImageName.value = name
  sourceImageUrl.value = URL.createObjectURL(blob)
  analysisResult.value = null
}

async function analyzeLoadedImage(): Promise<void> {
  if (!sourceImageBlob.value) {
    ElMessage.warning('请先上传图片或粘贴截图')
    return
  }

  if (!analysisGroupIds.value.length) {
    ElMessage.warning('至少要选择一组标准色卡')
    return
  }

  const runId = ++analysisRunId
  isAnalyzingImage.value = true
  analysisProgress.stage = 'counting'
  analysisProgress.current = 0
  analysisProgress.total = 1
  analysisProgress.ratio = 0
  analysisProgress.message = '准备开始分析图片颜色'

  try {
    const result = await analyzeImageColorDistribution(sourceImageBlob.value, {
      groupIds: [...analysisGroupIds.value],
      distanceMethod: analysisDistanceMethod.value,
      onProgress: (progress) => {
        if (runId !== analysisRunId) {
          return
        }

        analysisProgress.stage = progress.stage
        analysisProgress.current = progress.current
        analysisProgress.total = progress.total
        analysisProgress.ratio = progress.ratio
        analysisProgress.message = progress.message
      },
    })

    if (runId !== analysisRunId) {
      return
    }

    const nextProjectedImageUrl = URL.createObjectURL(result.projectedBlob)
    revokeObjectUrl(projectedImageUrl.value)
    projectedImageUrl.value = nextProjectedImageUrl
    analysisResult.value = result
    ElMessage.success('颜色分布分析完成')
  } catch (error) {
    if (runId === analysisRunId) {
      ElMessage.error(error instanceof Error ? error.message : '颜色分布分析失败')
    }
  } finally {
    if (runId === analysisRunId) {
      isAnalyzingImage.value = false
    }
  }
}

async function handleIncomingImage(blob: Blob, name: string): Promise<void> {
  updateSourceImage(blob, name)
  ElMessage.success('已载入图片，开始分析颜色分布')
  await analyzeLoadedImage()
}

function triggerImagePicker(): void {
  imageInputRef.value?.click()
}

async function handleImageInputChange(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''

  if (!file) {
    return
  }

  await handleIncomingImage(file, file.name)
}

async function handleWindowPaste(event: ClipboardEvent): Promise<void> {
  const items = event.clipboardData?.items
  if (!items) {
    return
  }

  for (const item of items) {
    if (!item.type.startsWith('image/')) {
      continue
    }

    const file = item.getAsFile()
    if (!file) {
      continue
    }

    const extension = file.type.split('/')[1] || 'png'
    await handleIncomingImage(file, `剪贴板图片.${extension}`)
    return
  }
}

function clearImageAnalysis(): void {
  analysisRunId += 1
  isAnalyzingImage.value = false
  sourceImageBlob.value = null
  sourceImageName.value = ''
  analysisResult.value = null
  analysisProgress.stage = 'counting'
  analysisProgress.current = 0
  analysisProgress.total = 1
  analysisProgress.ratio = 0
  analysisProgress.message = '等待载入图片'
  resetAnalysisUrls()
}

onMounted(() => {
  restorePersistedColorToolsState()
  window.addEventListener('paste', handleWindowPaste)
})

onUnmounted(() => {
  analysisRunId += 1
  window.removeEventListener('paste', handleWindowPaste)
  resetAnalysisUrls()
})
</script>

<template>
  <div class="color-tools-page">
    <section class="page-hero">
      <div class="hero-copy">
        <p class="hero-kicker">综合工具 / 颜色工具</p>
        <h1>颜色色卡与算法实验台</h1>
        <p class="hero-desc">
          基于 <code>pyxllib/cv/rgbfmt.py</code> 移植到纯前端，可直接做颜色查找、格式换算、相近标准色匹配、混色，以及图片颜色分布分析。
        </p>
      </div>
    </section>

    <div class="top-grid">
      <el-card class="panel-card">
        <template #header>
          <div class="panel-header">
            <span>挑选颜色</span>
            <el-button type="primary" link @click="copyValue(currentHex)">
              <el-icon><CopyDocument /></el-icon>
              复制 HEX
            </el-button>
          </div>
        </template>

        <div class="editor-grid">
          <div class="editor-preview" :style="getSwatchStyle(currentColor)">
            <div class="editor-picker-row">
              <StandardColorPickerPopover
                :model-value="currentHex"
                :visible="currentPickerVisible"
                placement="bottom-start"
                @update:model-value="handleCurrentPickerPreview"
                @update:visible="handleCurrentPickerVisibleChange"
              >
                <template #reference>
                  <button
                    type="button"
                    class="editor-picker-trigger"
                    :style="{ '--picker-color': currentHex }"
                    title="打开标准选色器"
                    aria-label="打开标准选色器"
                  >
                    <span class="editor-picker-trigger__swatch" />
                    <span class="editor-picker-trigger__label">标准选色</span>
                    <span class="editor-picker-trigger__caret" />
                  </button>
                </template>
              </StandardColorPickerPopover>
            </div>
          </div>

          <div class="editor-form">
            <div class="form-row">
              <label>HEX</label>
              <el-input v-model="hexDraft" @change="applyHexDraft" />
            </div>

            <div class="form-row">
              <label>R</label>
              <div class="channel-row">
                <el-slider v-model="currentColor.r" :min="0" :max="255" :step="1" />
                <el-input-number v-model="currentColor.r" :min="0" :max="255" />
              </div>
            </div>

            <div class="form-row">
              <label>G</label>
              <div class="channel-row">
                <el-slider v-model="currentColor.g" :min="0" :max="255" :step="1" />
                <el-input-number v-model="currentColor.g" :min="0" :max="255" />
              </div>
            </div>

            <div class="form-row">
              <label>B</label>
              <div class="channel-row">
                <el-slider v-model="currentColor.b" :min="0" :max="255" :step="1" />
                <el-input-number v-model="currentColor.b" :min="0" :max="255" />
              </div>
            </div>

            <div class="form-row">
              <label>VBA 正值</label>
              <el-input v-model="vbaPositiveDraft" @change="applyVbaDraft()" />
            </div>

            <div class="form-row">
              <label>VBA 负值</label>
              <el-input v-model="vbaNegativeDraft" @change="applyVbaDraft(true)" />
            </div>

            <div class="form-row">
              <label>百分比</label>
              <div class="plain-value">{{ percentagePercentLabel }}</div>
            </div>
          </div>
        </div>
      </el-card>

      <el-card class="panel-card">
        <template #header>
          <div class="panel-header">
            <span>相近标准色</span>
          </div>
        </template>

        <div class="similarity-list">
          <div
            v-for="(row, index) in similarityRows"
            :key="row.color.hex"
            class="similarity-card compact"
            :style="getSimilarityCardStyle(row.color)"
          >
            <div class="similarity-row-main">
              <span class="similarity-rank">{{ index + 1 }}.</span>
              <span class="similarity-name">{{ row.color.displayName }}</span>
              <span v-if="row.secondaryName" class="similarity-en">{{ row.secondaryName }}</span>
              <span class="similarity-distance">距离 {{ row.delta }}</span>
            </div>
            <button type="button" class="similarity-copy-btn" @click="copyValue(row.color.hex)">
              <el-icon><CopyDocument /></el-icon>
              复制 {{ row.color.hex }}
            </button>
          </div>
        </div>
      </el-card>
    </div>

    <el-card class="panel-card">
      <template #header>
        <div class="panel-header">
          <span>调色板</span>
        </div>
      </template>

      <div class="mix-composer">
        <div class="mix-section-head">
          <span class="mix-section-label">补全色</span>
          <span class="mix-section-meta">自动补 {{ mixFillWeight }}</span>
        </div>

        <div class="mix-fill-row">
          <div class="mix-single-row">
            <StandardColorPickerPopover
              :model-value="mixFillHex"
              :visible="mixFillPickerVisible"
              placement="bottom-start"
              @update:model-value="handleMixFillColorPreview"
              @update:visible="handleMixFillPickerVisibleChange"
            >
              <template #reference>
                <button
                  type="button"
                  class="mix-fill-trigger"
                  :style="getHexCardStyle(mixFillHex)"
                >
                  <div class="mix-fill-trigger__main">
                    <span class="mix-fill-trigger__name">{{ mixFillMappedInfo.mappedColor.displayName }}</span>
                    <span v-if="mixFillSecondaryName" class="mix-fill-trigger__secondary">{{ mixFillSecondaryName }}</span>
                    <span class="mix-fill-trigger__hex">{{ mixFillHex }}</span>
                  </div>
                  <span class="mix-fill-trigger__caret" />
                </button>
              </template>
            </StandardColorPickerPopover>
            <button
              type="button"
              class="similarity-copy-btn mix-copy-btn"
              :style="getContrastVarsStyle(mixFillHex)"
              @click="copyValue(mixFillHex)"
            >
              <el-icon><CopyDocument /></el-icon>
              复制 {{ mixFillHex }}
            </button>
          </div>
        </div>

        <div class="mix-section-head">
          <span class="mix-section-label">搭配色</span>
          <span class="mix-section-meta">当前总权重 {{ mixTotalWeight }}</span>
        </div>

        <div v-if="resolvedMixEntries.length" class="selected-list">
          <div v-for="entry in resolvedMixEntries" :key="entry.id" class="mix-selected-row">
            <StandardColorPickerPopover
              :model-value="entry.effectiveHex"
              :visible="activeMixEntryPickerId === entry.id"
              placement="bottom-start"
              @update:model-value="value => handleMixEntryColorPreview(entry, value)"
              @update:visible="visible => handleMixEntryPickerVisibleChange(entry, visible)"
            >
              <template #reference>
                <button
                  type="button"
                  class="mix-color-trigger"
                  :style="getHexCardStyle(entry.effectiveHex)"
                  :title="entry.effectiveHex"
                >
                  <div class="mix-color-trigger__main">
                    <span class="mix-color-trigger__primary">{{ entry.mappedInfo.mappedColor.displayName }}</span>
                    <span v-if="entry.secondaryName" class="mix-color-trigger__secondary">{{ entry.secondaryName }}</span>
                    <span class="mix-color-trigger__meta">{{ entry.effectiveHex }}</span>
                  </div>
                  <span class="mix-color-trigger__caret" />
                </button>
              </template>
            </StandardColorPickerPopover>

            <button
              type="button"
              class="similarity-copy-btn mix-copy-btn"
              :style="getContrastVarsStyle(entry.effectiveHex)"
              @click="copyValue(entry.effectiveHex)"
            >
              <el-icon><CopyDocument /></el-icon>
              复制 {{ entry.effectiveHex }}
            </button>

            <el-input-number
              :model-value="entry.weight"
              :min="0"
              :max="100"
              :step="5"
              size="small"
              controls-position="right"
              class="weight-input"
              @update:model-value="value => updateMixEntryWeight(entry.id, value)"
            />
            <el-button text :icon="Close" @click="removeMixEntry(entry.id)" />
          </div>
        </div>
        <div v-else class="mix-empty">还没有颜色，新增一条开始配比。</div>

        <div class="add-section">
          <el-button size="small" plain :icon="Plus" @click="addMixEntry">
            新增颜色
          </el-button>
          <span class="mix-add-hint">默认带入当前颜色，可再点色块修改。</span>
        </div>

        <div class="mix-section-head">
          <span class="mix-section-label">混合映射结果</span>
          <span class="mix-section-meta">自动计算</span>
        </div>

        <div class="mix-result-row">
          <div
            class="mix-preview-card"
            :style="getHexCardStyle(mixedPreview)"
            :title="mixedPreviewTooltip"
          >
            <div class="mix-preview-card__main">
              <span class="mix-preview-card__name">{{ mixedPreviewPrimaryText }}</span>
              <span v-if="mixedPreviewSecondaryName" class="mix-preview-card__en">{{ mixedPreviewSecondaryName }}</span>
              <span class="mix-preview-card__meta">{{ mixedPreviewHex }}</span>
              <span
                v-if="mixedPreviewMappedInfo.mappedColor.hex !== mixedPreviewHex"
                class="mix-preview-card__meta"
              >
                映射 {{ mixedPreviewMappedInfo.mappedColor.hex }}
              </span>
            </div>
          </div>
          <button
            type="button"
            class="similarity-copy-btn mix-copy-btn"
            :style="getContrastVarsStyle(mixedPreview)"
            @click="copyValue(mixedPreviewHex)"
          >
            <el-icon><CopyDocument /></el-icon>
            复制 {{ mixedPreviewHex }}
          </button>
        </div>

        <div class="panel-footer mix-panel-footer">
          <span>颜色会按权重自动混合；总权重不足 100 时自动补补全色。</span>
        </div>
      </div>
    </el-card>

    <el-card class="panel-card analysis-card">
      <template #header>
        <div class="panel-header panel-header-wrap">
          <div>
            <span>图片颜色分布</span>
            <p class="tab-hint">先统计图片中的唯一像素颜色，再映射到所选标准色卡组成的参考色集合。</p>
          </div>
          <div class="analysis-actions">
            <el-button type="primary" @click="triggerImagePicker">
              <el-icon><UploadFilled /></el-icon>
              上传图片
            </el-button>
            <el-button :disabled="!sourceImageBlob || isAnalyzingImage" @click="analyzeLoadedImage">
              重新分析
            </el-button>
            <el-button :disabled="!sourceImageBlob" @click="clearImageAnalysis">清空</el-button>
          </div>
        </div>
      </template>

      <input
        ref="imageInputRef"
        class="hidden-file-input"
        type="file"
        accept="image/*"
        @change="handleImageInputChange"
      >

      <div class="upload-zone" @click="triggerImagePicker">
        <div class="upload-icon-shell">
          <el-icon><Picture /></el-icon>
        </div>
        <div class="upload-copy">
          <strong>{{ sourceImageName || '上传一张图片，或直接 Ctrl+V 粘贴截图' }}</strong>
          <span>默认勾选三组色卡，默认使用 CIE76(Lab) 距离；透明像素不参与统计。</span>
        </div>
      </div>

      <div class="analysis-config">
        <div class="config-block">
          <label>参考色集合</label>
          <el-checkbox-group v-model="analysisGroupIds">
            <el-checkbox
              v-for="group in COLOR_GROUPS"
              :key="group.id"
              :label="group.id"
            >
              {{ group.shortLabel }}
            </el-checkbox>
          </el-checkbox-group>
          <span class="config-hint">当前并集后共有 {{ analysisPaletteSize }} 个标准颜色。</span>
        </div>

        <div class="config-block">
          <label>距离算法</label>
          <el-select v-model="analysisDistanceMethod" class="distance-select">
            <el-option
              v-for="method in DISTANCE_METHODS"
              :key="method.value"
              :label="method.label"
              :value="method.value"
            />
          </el-select>
          <span class="config-hint">
            {{ DISTANCE_METHODS.find(method => method.value === analysisDistanceMethod)?.description }}
          </span>
        </div>
      </div>

      <div v-if="isAnalyzingImage" class="progress-panel">
        <div class="progress-head">
          <strong>{{ analysisProgress.message }}</strong>
          <span>{{ analysisProgressPercent }}%</span>
        </div>
        <el-progress :percentage="analysisProgressPercent" :stroke-width="12" />
      </div>

      <el-empty
        v-if="!sourceImageUrl"
        description="还没有图片，上传后会自动开始分析颜色分布"
      />

      <template v-else>
        <div class="image-compare-grid">
          <div class="image-preview-card">
            <div class="image-preview-head">
              <strong>原图</strong>
              <span>{{ sourceImageName || '已载入图片' }}</span>
            </div>
            <img :src="sourceImageUrl" alt="原图预览" class="analysis-image">
          </div>

          <div class="image-preview-card">
            <div class="image-preview-head">
              <strong>投影后图片</strong>
              <div class="image-preview-actions">
                <a
                  v-if="projectedImageUrl"
                  :href="projectedImageUrl"
                  download="projected-color-distribution.png"
                  class="download-link"
                  @click.stop
                >
                  <el-icon><Download /></el-icon>
                  下载
                </a>
              </div>
            </div>
            <img
              v-if="projectedImageUrl"
              :src="projectedImageUrl"
              alt="投影后图片"
              class="analysis-image"
            >
            <div v-else class="analysis-placeholder">
              {{ isAnalyzingImage ? '正在生成投影图...' : '等待分析结果' }}
            </div>
          </div>
        </div>

        <template v-if="analysisResult">
          <div class="summary-grid">
            <div class="summary-box">
              <span class="value-label">图片尺寸</span>
              <strong>{{ analysisResult.width }} × {{ analysisResult.height }}</strong>
            </div>
            <div class="summary-box">
              <span class="value-label">有效像素</span>
              <strong>{{ formatInteger(analysisResult.opaquePixels) }}</strong>
            </div>
            <div class="summary-box">
              <span class="value-label">透明像素</span>
              <strong>{{ formatInteger(analysisResult.transparentPixels) }}</strong>
            </div>
            <div class="summary-box">
              <span class="value-label">原图唯一颜色</span>
              <strong>{{ formatInteger(analysisResult.uniqueSourceColorCount) }}</strong>
            </div>
            <div class="summary-box">
              <span class="value-label">投影后颜色种数</span>
              <strong>{{ formatInteger(analysisResult.uniqueProjectedColorCount) }}</strong>
            </div>
            <div class="summary-box">
              <span class="value-label">Top 10 覆盖率</span>
              <strong>{{ formatPercent(topCoverageSummary.top10) }}</strong>
            </div>
          </div>

          <div class="distribution-note">
            <span>Top 5 覆盖率 {{ formatPercent(topCoverageSummary.top5) }}</span>
            <span>当前参考色集合共 {{ analysisPaletteSize }} 个标准色</span>
            <span>统计表已按像素数从高到低排序</span>
          </div>

          <el-table :data="distributionRows" stripe class="distribution-table" max-height="560">
            <el-table-column prop="rank" label="#" width="70" />
            <el-table-column label="标准色卡" min-width="420">
              <template #default="{ row }">
                <div
                  class="similarity-card compact distribution-card-row"
                  :style="getSimilarityCardStyle(row.color)"
                  :title="`${row.zhLabel} / ${row.enLabel} / ${row.color.hex}`"
                >
                  <div class="similarity-row-main distribution-card-main">
                    <span class="similarity-name distribution-card-zh">{{ row.zhLabel }}</span>
                    <span v-if="row.enLabel !== '—'" class="similarity-en distribution-card-en">{{ row.enLabel }}</span>
                  </div>
                  <button type="button" class="similarity-copy-btn" @click="copyValue(row.color.hex)">
                    <el-icon><CopyDocument /></el-icon>
                    复制 {{ row.color.hex }}
                  </button>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="占比" width="110">
              <template #default="{ row }">
                {{ formatPercent(row.percent) }}
              </template>
            </el-table-column>
            <el-table-column label="像素数" width="140" align="right">
              <template #default="{ row }">
                {{ formatInteger(row.count) }}
              </template>
            </el-table-column>
          </el-table>
        </template>
      </template>
    </el-card>

    <el-card class="panel-card palette-card">
      <template #header>
        <div class="panel-header panel-header-wrap">
          <div>
            <span>标准色卡</span>
            <p class="tab-hint">分组直接对应 <code>rgbfmt.py</code> 的三段颜色表。</p>
          </div>
          <el-input
            v-model="paletteSearch"
            class="palette-search"
            placeholder="按 HEX 或中英文名筛选当前色卡"
            clearable
          />
        </div>
      </template>

      <el-tabs v-model="activePalette" class="palette-tabs">
        <el-tab-pane
          v-for="group in COLOR_GROUPS"
          :key="group.id"
          :label="`${group.shortLabel} (${groupPalettes[group.id].length})`"
          :name="group.id"
        >
          <div class="palette-toolbar">
            <p>{{ group.description }}</p>
            <span>当前展示 {{ filteredPalette.length }} 个颜色</span>
          </div>

          <div v-if="filteredPalette.length" class="palette-list">
            <button
              v-for="color in filteredPalette"
              :key="`${activePalette}-${color.hex}`"
              type="button"
              class="palette-item-row"
              :class="{ active: color.hex === currentHex }"
              :style="getSimilarityCardStyle(color)"
              @click="selectPaletteColor(color)"
            >
              <span class="palette-row-main">
                <span class="palette-name">{{ color.displayName }}</span>
                <span v-if="getPaletteSecondaryName(color)" class="palette-en">{{ getPaletteSecondaryName(color) }}</span>
              </span>
              <span class="similarity-copy-btn" @click.stop="copyValue(color.hex)">
                <el-icon><CopyDocument /></el-icon>
                复制 {{ color.hex }}
              </span>
            </button>
          </div>

          <el-empty v-else description="当前筛选条件下没有匹配颜色" />
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<style scoped>
.color-tools-page {
  min-height: 100%;
  padding: 24px;
  background:
    radial-gradient(circle at top right, rgba(255, 194, 119, 0.22), transparent 28%),
    radial-gradient(circle at top left, rgba(64, 158, 255, 0.16), transparent 24%),
    linear-gradient(180deg, #fffef8 0%, #f6f8fb 100%);
}

.page-hero {
  display: flex;
  align-items: stretch;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 24px;
  padding: 24px 28px;
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.86);
  box-shadow: 0 16px 48px rgba(18, 34, 66, 0.08);
  backdrop-filter: blur(10px);
}

.hero-copy {
  max-width: 760px;
}

.hero-kicker {
  margin: 0 0 8px;
  font-size: 13px;
  color: #cf6c2b;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.hero-copy h1 {
  margin: 0 0 10px;
  font-size: 34px;
  line-height: 1.15;
  color: #243046;
}

.hero-desc {
  margin: 0;
  color: #56647a;
  line-height: 1.7;
}

.top-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
  margin-bottom: 20px;
}

.panel-card {
  border: none;
  border-radius: 20px;
  box-shadow: 0 14px 42px rgba(18, 34, 66, 0.08);
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  font-weight: 700;
  color: #243046;
}

.panel-header-wrap {
  align-items: flex-start;
  flex-wrap: wrap;
}

.panel-hint,
.tab-hint,
.config-hint {
  font-size: 12px;
  color: #7a8799;
  font-weight: 500;
}

.tab-hint {
  margin: 6px 0 0;
}

.editor-grid {
  display: grid;
  grid-template-columns: minmax(220px, 0.8fr) minmax(0, 1.2fr);
  gap: 20px;
}

.editor-preview {
  border-radius: 18px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-height: 240px;
}

.editor-picker-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.editor-picker-trigger {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  min-height: 42px;
  padding: 0 14px;
  border: 1px solid rgba(255, 255, 255, 0.42);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.18);
  color: inherit;
  cursor: pointer;
  transition: border-color 0.18s ease, background-color 0.18s ease, box-shadow 0.18s ease;
}

.editor-picker-trigger:hover {
  border-color: rgba(255, 255, 255, 0.58);
  background: rgba(255, 255, 255, 0.28);
}

.editor-picker-trigger:focus-visible {
  outline: none;
  border-color: rgba(255, 255, 255, 0.76);
  box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.18);
}

.editor-picker-trigger__swatch {
  width: 18px;
  height: 18px;
  border-radius: 6px;
  background: var(--picker-color);
  border: 1px solid rgba(15, 23, 42, 0.14);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.28);
  flex-shrink: 0;
}

.editor-picker-trigger__label {
  font-size: 13px;
  font-weight: 600;
}

.editor-picker-trigger__caret {
  width: 0;
  height: 0;
  margin-left: 2px;
  border-left: 4px solid transparent;
  border-right: 4px solid transparent;
  border-top: 5px solid rgba(255, 255, 255, 0.82);
}

.editor-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-row {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr);
  gap: 12px;
  align-items: center;
}

.form-row label,
.config-block label {
  font-size: 13px;
  color: #5d697b;
  font-weight: 600;
}

.channel-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.channel-row :deep(.el-slider) {
  flex: 1;
}

.channel-row :deep(.el-input-number) {
  width: 120px;
  flex-shrink: 0;
}

.plain-value {
  font-size: 14px;
  line-height: 1.4;
  color: #243046;
  user-select: text;
}

.conversion-grid,
.summary-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.value-box,
.summary-box {
  padding: 16px;
  border-radius: 16px;
  background: linear-gradient(180deg, #fbfcfe 0%, #f3f6fa 100%);
  border: 1px solid #e7edf5;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.value-box-wide {
  grid-column: 1 / -1;
}

.value-label {
  font-size: 12px;
  color: #7a8799;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.similarity-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.similarity-card {
  padding: 10px 12px;
  border-radius: 12px;
  background: linear-gradient(180deg, #fcfdff 0%, #f4f7fb 100%);
  border: 1px solid #e6edf6;
}

.similarity-card.compact {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-color: var(--sim-chip-border);
  color: var(--sim-fg);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.16);
}
.distribution-note {
  color: #607086;
  line-height: 1.6;
}

.result-card {
  border-radius: 16px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 160px;
  border: 1px solid rgba(255, 255, 255, 0.42);
}

.similarity-row-main {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.similarity-rank {
  font-size: 14px;
  line-height: 1.25;
  color: var(--sim-fg);
  font-weight: 700;
  min-width: 20px;
}

.similarity-name {
  font-size: 14px;
  line-height: 1.2;
  color: var(--sim-fg);
  font-weight: 700;
}

.similarity-en {
  font-size: 12px;
  line-height: 1.2;
  color: var(--sim-fg-muted);
}

.similarity-distance {
  font-size: 13px;
  line-height: 1.2;
  color: var(--sim-fg-muted);
}

.similarity-copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border-radius: 999px;
  border: 1px solid var(--sim-chip-border);
  background: var(--sim-chip-bg);
  color: var(--sim-fg);
  font-size: 12px;
  line-height: 1.2;
  white-space: nowrap;
  cursor: pointer;
  transition: background-color 0.18s ease, transform 0.18s ease;
}

.similarity-copy-btn:hover {
  transform: translateY(-1px);
}

.similarity-copy-btn:focus-visible {
  outline: 2px solid var(--sim-chip-border);
  outline-offset: 1px;
}

.mix-composer,
.config-block {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.mix-preview-card,
.mix-color-trigger,
.mix-fill-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
  border-radius: 12px;
  border: 1px solid var(--sim-chip-border);
  color: var(--sim-fg);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.16);
}

.mix-preview-card,
.mix-fill-trigger {
  min-height: 40px;
  padding: 8px 10px;
}

.mix-preview-card__main,
.mix-color-trigger__main,
.mix-fill-trigger__main {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.mix-preview-card__name,
.mix-color-trigger__primary,
.mix-fill-trigger__name {
  font-size: 13px;
  line-height: 1.2;
  font-weight: 700;
  color: var(--sim-fg);
}

.mix-preview-card__en,
.mix-preview-card__meta,
.mix-color-trigger__secondary,
.mix-color-trigger__meta,
.mix-fill-trigger__secondary,
.mix-fill-trigger__hex {
  font-size: 12px;
  line-height: 1.2;
  color: var(--sim-fg-muted);
}

.mix-preview-card__name,
.mix-preview-card__en,
.mix-preview-card__meta,
.mix-color-trigger__primary,
.mix-color-trigger__secondary,
.mix-color-trigger__meta,
.mix-fill-trigger__name,
.mix-fill-trigger__secondary,
.mix-fill-trigger__hex {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.selected-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.mix-single-row,
.mix-result-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 152px 104px 28px;
  align-items: center;
  gap: 8px;
}

.mix-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: #607086;
}

.mix-section-label {
  font-size: 13px;
  line-height: 1.2;
  font-weight: 700;
  color: #243046;
}

.mix-section-meta {
  font-size: 12px;
  line-height: 1.2;
  color: #7a8799;
  white-space: nowrap;
}

.mix-selected-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 152px 104px 28px;
  align-items: center;
  gap: 8px;
}

.mix-color-trigger,
.mix-fill-trigger {
  width: 100%;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}

.mix-color-trigger {
  min-height: 40px;
  padding: 8px 10px;
}

.mix-copy-btn {
  width: 152px;
  justify-content: center;
  align-self: stretch;
}

.mix-color-trigger:hover,
.mix-fill-trigger:hover {
  transform: translateY(-1px);
}

.mix-color-trigger__caret,
.mix-fill-trigger__caret {
  width: 0;
  height: 0;
  margin-left: auto;
  border-left: 4px solid transparent;
  border-right: 4px solid transparent;
  border-top: 5px solid var(--sim-fg-muted);
  flex: none;
}

.mix-empty {
  font-size: 12px;
  color: #909399;
  padding: 6px 2px;
}

.add-section {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.mix-add-hint {
  font-size: 12px;
  color: #909399;
  line-height: 1.4;
}

.mix-fill-row {
  display: block;
}

.weight-input {
  width: 104px;
}

.mix-panel-footer {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 12px;
  font-size: 12px;
  color: #909399;
  line-height: 1.4;
  flex-wrap: wrap;
}

.analysis-card,
.palette-card {
  margin-top: 20px;
}

.analysis-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.hidden-file-input {
  display: none;
}

.upload-zone {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px;
  margin-bottom: 18px;
  border: 1px dashed #c8d7eb;
  border-radius: 18px;
  background: linear-gradient(180deg, #fdfefe 0%, #f4f8fc 100%);
  cursor: pointer;
  transition: border-color 0.18s ease, transform 0.18s ease, box-shadow 0.18s ease;
}

.upload-zone:hover {
  border-color: #409eff;
  transform: translateY(-1px);
  box-shadow: 0 12px 24px rgba(64, 158, 255, 0.12);
}

.upload-icon-shell {
  width: 54px;
  height: 54px;
  border-radius: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(180deg, #409eff 0%, #73b8ff 100%);
  color: #fff;
  font-size: 24px;
  flex-shrink: 0;
}

.upload-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.upload-copy strong {
  color: #243046;
}

.upload-copy span {
  color: #607086;
  line-height: 1.6;
}

.analysis-config {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(260px, 0.8fr);
  gap: 20px;
  margin-bottom: 18px;
}

.distance-select {
  width: 100%;
}

.progress-panel {
  margin-bottom: 18px;
  padding: 16px;
  border-radius: 16px;
  background: linear-gradient(180deg, #fefaf0 0%, #f8f2e2 100%);
  border: 1px solid #ecd9a5;
}

.progress-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
  color: #6d5320;
}

.image-compare-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
  margin-bottom: 18px;
}

.image-preview-card {
  padding: 16px;
  border-radius: 18px;
  background: linear-gradient(180deg, #fcfdff 0%, #f4f7fb 100%);
  border: 1px solid #e6edf6;
}

.image-preview-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  margin-bottom: 12px;
  color: #243046;
}

.image-preview-head span {
  color: #607086;
  font-size: 13px;
}

.image-preview-actions {
  display: flex;
  gap: 10px;
  align-items: center;
}

.download-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #409eff;
  text-decoration: none;
}

.analysis-image,
.analysis-placeholder {
  width: 100%;
  min-height: 240px;
  max-height: 560px;
  border-radius: 14px;
  border: 1px solid #dfe8f3;
  background: #fff;
}

.analysis-image {
  object-fit: contain;
}

.analysis-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #7a8799;
  background: repeating-linear-gradient(
    -45deg,
    #f7f9fc,
    #f7f9fc 12px,
    #eff4f9 12px,
    #eff4f9 24px
  );
}

.distribution-note {
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
  margin: 14px 0 16px;
}

.distribution-table {
  width: 100%;
}

.distribution-card-row {
  margin: 4px 0;
}

.distribution-card-main {
  gap: 6px;
}

.distribution-card-zh,
.distribution-card-en {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.palette-search {
  width: min(360px, 100%);
}

.palette-toolbar {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  margin-bottom: 18px;
  color: #607086;
}

.palette-toolbar p {
  margin: 0;
}

.palette-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 8px;
}

.palette-item-row {
  min-width: 0;
  border-radius: 12px;
  border: 1px solid var(--sim-chip-border);
  background: transparent;
  padding: 8px 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  text-align: left;
  cursor: pointer;
  color: var(--sim-fg);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.16);
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}

.palette-item-row:hover {
  transform: translateY(-1px);
}

.palette-item-row.active {
  box-shadow: inset 0 0 0 2px rgba(255, 255, 255, 0.42);
}

.palette-row-main {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
  flex: 1;
}

.palette-name {
  font-size: 13px;
  line-height: 1.2;
  font-weight: 700;
  color: var(--sim-fg);
}

.palette-en {
  font-size: 11px;
  line-height: 1.2;
  color: var(--sim-fg-muted);
}

@media (max-width: 1100px) {
  .page-hero,
  .top-grid,
  .editor-grid,
  .analysis-config,
  .image-compare-grid {
    grid-template-columns: 1fr;
    flex-direction: column;
  }
}

@media (max-width: 768px) {
  .color-tools-page {
    padding: 16px;
  }

  .page-hero {
    padding: 20px;
  }

  .conversion-grid,
  .summary-grid {
    grid-template-columns: 1fr;
  }

  .form-row {
    grid-template-columns: 1fr;
  }

  .similarity-card.compact,
  .palette-toolbar,
  .panel-header,
  .image-preview-head {
    flex-direction: column;
    align-items: flex-start;
  }

  .similarity-card.compact .similarity-copy-btn {
    align-self: stretch;
    justify-content: center;
  }

  .palette-item-row {
    flex-direction: column;
    align-items: flex-start;
  }

  .palette-item-row .similarity-copy-btn {
    align-self: stretch;
    justify-content: center;
  }

  .channel-row,
  .upload-zone {
    flex-direction: column;
    align-items: stretch;
  }

  .editor-picker-row {
    flex-direction: column;
    align-items: flex-start;
  }

  .mix-section-head,
  .mix-fill-trigger,
  .mix-fill-trigger__main,
  .mix-preview-card {
    align-items: flex-start;
    flex-direction: column;
  }

  .mix-single-row,
  .mix-result-row,
  .mix-selected-row {
    grid-template-columns: 1fr;
  }

  .mix-copy-btn,
  .mix-preview-card .similarity-copy-btn {
    width: 100%;
    justify-content: center;
  }

  .mix-section-meta {
    align-self: stretch;
    white-space: normal;
  }

  .channel-row :deep(.el-input-number) {
    width: 100%;
  }
}
</style>
