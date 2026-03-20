<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { CopyDocument, Download, Picture, UploadFilled } from '@element-plus/icons-vue'
import {
  analyzeImageColorDistribution,
  getTopCoveragePercent,
  type AnalysisProgress,
  type ColorDistributionRow,
  type ImageColorDistributionResult,
} from '@/utils/colorDistribution'
import {
  COLOR_GROUPS,
  DISTANCE_METHODS,
  createRgbColor,
  distance,
  findSimilarStandardColor,
  fromHex,
  getColorMatchesByHex,
  getGroupPalette,
  getPaletteForGroups,
  getReadableTextColor,
  getRelativeColorDescription,
  lightenColor,
  mixColors,
  parseColorInput,
  toHex,
  toPercentage,
  toVbaValue,
  type ColorGroupId,
  type DistanceMethod,
  type RgbColor,
  type StandardColor,
} from '@/utils/colorToolkit'

const currentColor = reactive(createRgbColor(123, 45, 84))
const activePalette = ref<ColorGroupId>('core-zh')
const paletteSearch = ref('')
const quickInput = ref('')
const hexDraft = ref('#7B2D54')
const preciseMode = ref(false)
const lightRatio = ref(1)
const mixRatio = ref(1)
const mixTargetHex = ref('#FFFFFF')
const mixTargetDraft = ref('#FFFFFF')

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
const pickerHex = computed({
  get: () => currentHex.value,
  set: (value: string) => {
    try {
      setCurrentColor(fromHex(value))
    } catch {
      ElMessage.error('颜色选择器返回了无效颜色')
    }
  },
})

watch(
  () => [currentColor.r, currentColor.g, currentColor.b],
  () => {
    hexDraft.value = currentHex.value
  },
  { immediate: true },
)

watch(
  mixTargetHex,
  (value) => {
    mixTargetDraft.value = value
  },
  { immediate: true },
)

const currentRgbLabel = computed(() => `(${currentColor.r}, ${currentColor.g}, ${currentColor.b})`)
const currentCssRgb = computed(() => `rgb(${currentColor.r} ${currentColor.g} ${currentColor.b})`)
const percentageValues = computed(() => toPercentage(currentColor))
const percentageLabel = computed(() => percentageValues.value.map(value => value.toFixed(6)).join(', '))
const percentagePercentLabel = computed(() => percentageValues.value.map(value => `${(value * 100).toFixed(2)}%`).join(' / '))
const currentMatches = computed(() => getColorMatchesByHex(currentHex.value))
const mixTargetColor = computed(() => {
  try {
    return fromHex(mixTargetHex.value)
  } catch {
    return createRgbColor(255, 255, 255)
  }
})

const exactMatchRows = computed(() => COLOR_GROUPS.map(group => ({
  group,
  color: currentMatches.value[group.id],
})))

const similarityRows = computed(() => COLOR_GROUPS.map((group) => {
  const color = findSimilarStandardColor(currentColor, {
    range: group.range,
    preciseMode: preciseMode.value,
  })

  return {
    group,
    rangeLabel: group.range === 0
      ? '范围 0：基础中文'
      : group.range === 1
        ? '范围 1：基础中文 + 扩展中文'
        : '范围 2：全量英文',
    color,
    relative: getRelativeColorDescription(currentColor, color, {
      range: group.range,
      preciseMode: preciseMode.value,
    }),
    delta: distance(currentColor, color).toFixed(2),
  }
}))

const filteredPalette = computed(() => {
  const colors = groupPalettes[activePalette.value]
  const keyword = paletteSearch.value.trim().toLowerCase()
  if (!keyword) {
    return colors
  }

  return colors.filter(color => color.hex.toLowerCase().includes(keyword)
    || color.names.some(name => name.toLowerCase().includes(keyword)))
})

const lightPreview = computed(() => lightenColor(currentColor, lightRatio.value))
const mixedPreview = computed(() => mixColors(currentColor, mixTargetColor.value, mixRatio.value))
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

function getAliasText(color: StandardColor): string {
  return color.names.slice(1, 4).join(' / ')
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(2)}%`
}

function formatInteger(value: number): string {
  return new Intl.NumberFormat('zh-CN').format(value)
}

function applyQuickInput(): void {
  const parsed = parseColorInput(quickInput.value)
  if (!parsed) {
    ElMessage.error('未识别输入内容，支持 HEX、RGB、VBA 和标准颜色名')
    return
  }

  setCurrentColor(parsed)
  ElMessage.success('已应用颜色')
}

function applyHexDraft(): void {
  try {
    setCurrentColor(fromHex(hexDraft.value))
  } catch (error) {
    hexDraft.value = currentHex.value
    ElMessage.error(error instanceof Error ? error.message : 'HEX 颜色格式错误')
  }
}

function applyMixTargetDraft(): void {
  try {
    mixTargetHex.value = toHex(fromHex(mixTargetDraft.value))
  } catch (error) {
    mixTargetDraft.value = mixTargetHex.value
    ElMessage.error(error instanceof Error ? error.message : '混入颜色 HEX 格式错误')
  }
}

function selectPaletteColor(color: StandardColor): void {
  setCurrentColor(color)
  quickInput.value = color.displayName
}

function usePreviewColor(color: RgbColor): void {
  setCurrentColor(color)
  ElMessage.success('已将结果颜色设为当前颜色')
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

function getRowSwatchStyle(row: ColorDistributionRow): Record<string, string> {
  return getSwatchStyle(row.color)
}

onMounted(() => {
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
          基于 <code>pyxllib/cv/rgbfmt.py</code> 移植到纯前端，可直接做颜色查找、格式换算、相近标准色匹配、混色、提亮，以及图片颜色分布分析。
        </p>
      </div>
      <div class="hero-current" :style="getSwatchStyle(currentColor)">
        <div class="hero-swatch" />
        <div class="hero-values">
          <strong>{{ currentHex }}</strong>
          <span>{{ currentRgbLabel }}</span>
        </div>
      </div>
    </section>

    <div class="top-grid">
      <el-card class="panel-card">
        <template #header>
          <div class="panel-header">
            <span>当前颜色</span>
            <el-button type="primary" link @click="copyValue(currentHex)">
              <el-icon><CopyDocument /></el-icon>
              复制 HEX
            </el-button>
          </div>
        </template>

        <div class="editor-grid">
          <div class="editor-preview" :style="getSwatchStyle(currentColor)">
            <el-color-picker v-model="pickerHex" size="large" />
            <div class="preview-labels">
              <strong>{{ currentHex }}</strong>
              <span>{{ currentRgbLabel }}</span>
              <span>{{ currentCssRgb }}</span>
            </div>
          </div>

          <div class="editor-form">
            <div class="form-row">
              <label>快速输入</label>
              <el-input
                v-model="quickInput"
                placeholder="支持 #7B2D54 / 123,45,84 / -11260549 / 紫罗兰色"
                @keyup.enter="applyQuickInput"
              >
                <template #append>
                  <el-button @click="applyQuickInput">应用</el-button>
                </template>
              </el-input>
            </div>

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
          </div>
        </div>
      </el-card>

      <el-card class="panel-card">
        <template #header>
          <div class="panel-header">
            <span>格式转换</span>
            <span class="panel-hint">对应 RGB / HEX / VBA / 百分比转换</span>
          </div>
        </template>

        <div class="conversion-grid">
          <div class="value-box">
            <span class="value-label">HEX</span>
            <strong>{{ currentHex }}</strong>
          </div>
          <div class="value-box">
            <span class="value-label">RGB</span>
            <strong>{{ currentRgbLabel }}</strong>
          </div>
          <div class="value-box">
            <span class="value-label">CSS</span>
            <strong>{{ currentCssRgb }}</strong>
          </div>
          <div class="value-box">
            <span class="value-label">VBA 正值</span>
            <strong>{{ toVbaValue(currentColor) }}</strong>
          </div>
          <div class="value-box">
            <span class="value-label">VBA 负值</span>
            <strong>{{ toVbaValue(currentColor, true) }}</strong>
          </div>
          <div class="value-box">
            <span class="value-label">百分比 (0~1)</span>
            <strong>{{ percentageLabel }}</strong>
          </div>
          <div class="value-box value-box-wide">
            <span class="value-label">百分比 (%)</span>
            <strong>{{ percentagePercentLabel }}</strong>
          </div>
        </div>
      </el-card>
    </div>

    <div class="mid-grid">
      <el-card class="panel-card">
        <template #header>
          <div class="panel-header">
            <span>精确命名命中</span>
            <span class="panel-hint">当前颜色是否正好在各色卡中存在</span>
          </div>
        </template>

        <div class="match-list">
          <div v-for="row in exactMatchRows" :key="row.group.id" class="match-row">
            <div class="match-meta">
              <strong>{{ row.group.shortLabel }}</strong>
              <span>{{ row.group.description }}</span>
            </div>
            <div v-if="row.color" class="match-chip" :style="getSwatchStyle(row.color)">
              <span>{{ row.color.displayName }}</span>
              <small>{{ row.color.hex }}</small>
              <span class="color-card-action" @click.stop="copyValue(row.color.hex)">
                <el-icon><CopyDocument /></el-icon>
                复制 HEX
              </span>
            </div>
            <span v-else class="match-miss">当前 HEX 未命中该组精确色卡</span>
          </div>
        </div>
      </el-card>

      <el-card class="panel-card">
        <template #header>
          <div class="panel-header">
            <span>相近标准色</span>
            <el-switch
              v-model="preciseMode"
              active-text="精准距离"
              inactive-text="快速距离"
            />
          </div>
        </template>

        <div class="similarity-list">
          <div v-for="row in similarityRows" :key="row.group.id" class="similarity-card">
            <div class="similarity-top">
              <div>
                <strong>{{ row.rangeLabel }}</strong>
                <p>{{ row.color.displayName }} · {{ row.color.hex }}</p>
              </div>
              <div class="mini-swatch" :style="getSwatchStyle(row.color)">
                <span>{{ row.color.displayName }}</span>
                <span class="color-card-action" @click.stop="copyValue(row.color.hex)">
                  <el-icon><CopyDocument /></el-icon>
                  复制 HEX
                </span>
              </div>
            </div>
            <div class="similarity-desc">{{ row.relative }}</div>
            <div class="similarity-distance">色彩距离：{{ row.delta }}</div>
          </div>
        </div>
      </el-card>
    </div>

    <el-card class="panel-card">
      <template #header>
        <div class="panel-header">
          <span>混色与提亮</span>
          <span class="panel-hint">对应 mixtures / light</span>
        </div>
      </template>

      <div class="blend-grid">
        <div class="blend-column">
          <h3>提亮</h3>
          <div class="blend-control">
            <label>白色权重</label>
            <div class="channel-row">
              <el-slider v-model="lightRatio" :min="0" :max="6" :step="0.1" />
              <el-input-number v-model="lightRatio" :min="0" :max="6" :step="0.1" />
            </div>
          </div>
          <div class="result-card" :style="getSwatchStyle(lightPreview)">
            <strong>{{ toHex(lightPreview) }}</strong>
            <span>RGB {{ `(${lightPreview.r}, ${lightPreview.g}, ${lightPreview.b})` }}</span>
          </div>
          <el-button type="primary" plain @click="usePreviewColor(lightPreview)">使用提亮结果</el-button>
        </div>

        <div class="blend-column">
          <h3>混色</h3>
          <div class="blend-control">
            <label>混入颜色</label>
            <div class="blend-picker-row">
              <el-color-picker v-model="mixTargetHex" />
              <el-input v-model="mixTargetDraft" @change="applyMixTargetDraft" />
            </div>
          </div>
          <div class="blend-control">
            <label>混入权重</label>
            <div class="channel-row">
              <el-slider v-model="mixRatio" :min="0" :max="6" :step="0.1" />
              <el-input-number v-model="mixRatio" :min="0" :max="6" :step="0.1" />
            </div>
          </div>
          <div class="result-card" :style="getSwatchStyle(mixedPreview)">
            <strong>{{ toHex(mixedPreview) }}</strong>
            <span>RGB {{ `(${mixedPreview.r}, ${mixedPreview.g}, ${mixedPreview.b})` }}</span>
          </div>
          <el-button type="primary" plain @click="usePreviewColor(mixedPreview)">使用混色结果</el-button>
        </div>
      </div>
    </el-card>

    <el-card class="panel-card analysis-card">
      <template #header>
        <div class="panel-header panel-header-wrap">
          <div>
            <span>图片颜色分布</span>
            <p class="tab-hint">先统计唯一像素颜色，再投影到勾选色卡并集构成的标准色空间 A。</p>
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
          <label>基准色卡 A</label>
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
            <span>颜色空间 A 基于 {{ analysisPaletteSize }} 个标准色</span>
            <span>统计表已按像素数从高到低排序</span>
          </div>

          <el-table :data="distributionRows" stripe class="distribution-table" max-height="560">
            <el-table-column prop="rank" label="#" width="70" />
            <el-table-column label="颜色卡片" width="140">
              <template #default="{ row }">
                <div class="table-swatch" :style="getRowSwatchStyle(row)">
                  {{ row.color.displayName }}
                </div>
              </template>
            </el-table-column>
            <el-table-column label="中文名" min-width="180">
              <template #default="{ row }">
                {{ row.zhLabel }}
              </template>
            </el-table-column>
            <el-table-column label="英文名" min-width="220">
              <template #default="{ row }">
                {{ row.enLabel }}
              </template>
            </el-table-column>
            <el-table-column label="HEX" width="120">
              <template #default="{ row }">
                <el-tag class="copyable-hex-tag" @click="copyValue(row.color.hex)">
                  {{ row.color.hex }}
                </el-tag>
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
            placeholder="按 HEX 或名字筛选当前色卡"
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

          <div v-if="filteredPalette.length" class="palette-grid">
            <button
              v-for="color in filteredPalette"
              :key="`${activePalette}-${color.hex}`"
              type="button"
              class="palette-item"
              :class="{ active: color.hex === currentHex }"
              @click="selectPaletteColor(color)"
            >
              <span class="palette-swatch" :style="{ background: color.hex }" />
              <span class="palette-name">{{ color.displayName }}</span>
              <span class="palette-hex">{{ color.hex }}</span>
              <span v-if="getAliasText(color)" class="palette-alias">
                {{ getAliasText(color) }}
              </span>
              <span class="palette-copy-action" @click.stop="copyValue(color.hex)">
                <el-icon><CopyDocument /></el-icon>
                复制 HEX
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

.hero-current {
  min-width: 240px;
  border-radius: 22px;
  padding: 22px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.45);
}

.hero-swatch {
  width: 100%;
  height: 96px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.28);
  border: 1px solid rgba(255, 255, 255, 0.36);
}

.hero-values {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 18px;
}

.top-grid,
.mid-grid {
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

.preview-labels {
  display: flex;
  flex-direction: column;
  gap: 6px;
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
.blend-control label,
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

.match-list,
.similarity-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.match-row,
.similarity-card {
  padding: 16px;
  border-radius: 18px;
  background: linear-gradient(180deg, #fcfdff 0%, #f4f7fb 100%);
  border: 1px solid #e6edf6;
}

.match-row {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  align-items: center;
}

.match-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.match-meta span,
.match-miss,
.similarity-desc,
.similarity-distance,
.distribution-note {
  color: #607086;
  line-height: 1.6;
}

.match-chip,
.mini-swatch,
.result-card,
.table-swatch {
  border-radius: 16px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 160px;
  border: 1px solid rgba(255, 255, 255, 0.42);
}

.table-swatch {
  min-width: 0;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
}

.color-card-action,
.palette-copy-action {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  padding: 6px 10px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.28);
  border: 1px solid rgba(255, 255, 255, 0.4);
  cursor: pointer;
  user-select: none;
}

.color-card-action:hover,
.palette-copy-action:hover {
  background: rgba(255, 255, 255, 0.4);
}

.similarity-top {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  margin-bottom: 10px;
}

.similarity-top p {
  margin: 6px 0 0;
  color: #607086;
}

.mini-swatch {
  max-width: 220px;
  justify-content: center;
}

.copyable-hex-tag {
  cursor: pointer;
}

.blend-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
}

.blend-column {
  padding: 18px;
  border-radius: 18px;
  background: linear-gradient(180deg, #fbfcfe 0%, #f3f6fa 100%);
  border: 1px solid #e6edf6;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.blend-column h3 {
  margin: 0;
  color: #243046;
}

.blend-control,
.config-block {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.blend-picker-row {
  display: flex;
  gap: 12px;
  align-items: center;
}

.blend-picker-row :deep(.el-input) {
  flex: 1;
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

.palette-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 12px;
}

.palette-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 7px;
  padding: 14px;
  border-radius: 16px;
  border: 1px solid #e5ebf3;
  background: #fff;
  text-align: left;
  cursor: pointer;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
}

.palette-item:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 28px rgba(18, 34, 66, 0.1);
  border-color: #cdd9ea;
}

.palette-item.active {
  border-color: #409eff;
  box-shadow: 0 12px 28px rgba(64, 158, 255, 0.16);
}

.palette-swatch {
  width: 100%;
  height: 64px;
  border-radius: 12px;
  border: 1px solid rgba(15, 23, 42, 0.08);
}

.palette-name {
  font-weight: 700;
  color: #243046;
}

.palette-hex {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  color: #607086;
}

.palette-alias {
  font-size: 12px;
  color: #8090a6;
  line-height: 1.5;
}

.palette-copy-action {
  background: #eef5ff;
  border-color: #d6e6ff;
  color: #2f5ea5;
}

@media (max-width: 1100px) {
  .page-hero,
  .top-grid,
  .mid-grid,
  .blend-grid,
  .editor-grid,
  .analysis-config,
  .image-compare-grid {
    grid-template-columns: 1fr;
    flex-direction: column;
  }

  .hero-current {
    min-width: 0;
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
  .summary-grid,
  .palette-grid {
    grid-template-columns: 1fr;
  }

  .form-row {
    grid-template-columns: 1fr;
  }

  .match-row,
  .similarity-top,
  .palette-toolbar,
  .panel-header,
  .image-preview-head {
    flex-direction: column;
    align-items: flex-start;
  }

  .channel-row,
  .blend-picker-row,
  .upload-zone {
    flex-direction: column;
    align-items: stretch;
  }

  .channel-row :deep(.el-input-number) {
    width: 100%;
  }
}
</style>
