import {
  buildNearestColorMatcher,
  fromRgbIntKey,
  getPaletteForGroups,
  type ColorGroupId,
  type DistanceMethod,
  type StandardColor,
} from '@/utils/colorToolkit'

export type AnalysisStage = 'counting' | 'matching' | 'rendering'

export interface AnalysisProgress {
  stage: AnalysisStage
  current: number
  total: number
  ratio: number
  message: string
}

export interface ColorDistributionRow {
  rank: number
  color: StandardColor
  count: number
  percent: number
}

export interface ImageColorDistributionResult {
  width: number
  height: number
  totalPixels: number
  opaquePixels: number
  transparentPixels: number
  uniqueSourceColorCount: number
  uniqueProjectedColorCount: number
  paletteSize: number
  rows: ColorDistributionRow[]
  projectedBlob: Blob
}

function reportProgress(
  onProgress: ((progress: AnalysisProgress) => void) | undefined,
  stage: AnalysisStage,
  current: number,
  total: number,
  message: string,
): void {
  if (!onProgress) {
    return
  }

  onProgress({
    stage,
    current,
    total,
    ratio: total > 0 ? current / total : 0,
    message,
  })
}

async function yieldToMainThread(): Promise<void> {
  await new Promise(resolve => setTimeout(resolve, 0))
}

async function loadImageFromBlob(blob: Blob): Promise<HTMLImageElement> {
  const url = URL.createObjectURL(blob)

  try {
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const img = new Image()
      img.onload = () => resolve(img)
      img.onerror = () => reject(new Error('图片加载失败'))
      img.src = url
    })

    return image
  } finally {
    URL.revokeObjectURL(url)
  }
}

async function canvasToBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  const blob = await new Promise<Blob | null>((resolve) => {
    canvas.toBlob((value) => resolve(value), 'image/png')
  })

  if (!blob) {
    throw new Error('生成投影图片失败')
  }

  return blob
}

export async function analyzeImageColorDistribution(
  blob: Blob,
  options: {
    groupIds: ColorGroupId[]
    distanceMethod: DistanceMethod
    onProgress?: (progress: AnalysisProgress) => void
  },
): Promise<ImageColorDistributionResult> {
  const { groupIds, distanceMethod, onProgress } = options
  const palette = getPaletteForGroups(groupIds)
  if (!palette.length) {
    throw new Error('请至少选择一组标准色卡')
  }

  const image = await loadImageFromBlob(blob)
  const width = image.naturalWidth || image.width
  const height = image.naturalHeight || image.height

  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height

  const context = canvas.getContext('2d', { willReadFrequently: true })
  if (!context) {
    throw new Error('浏览器不支持 Canvas 2D 上下文')
  }

  context.drawImage(image, 0, 0, width, height)
  const imageData = context.getImageData(0, 0, width, height)
  const data = imageData.data
  const totalPixels = width * height

  const sourceColorCounts = new Map<number, number>()
  let opaquePixels = 0
  let transparentPixels = 0

  reportProgress(onProgress, 'counting', 0, totalPixels, '正在统计原图像素颜色')

  for (let pixelIndex = 0; pixelIndex < totalPixels; pixelIndex += 1) {
    const offset = pixelIndex * 4
    const alpha = data[offset + 3]
    if (alpha === 0) {
      transparentPixels += 1
    } else {
      opaquePixels += 1
      const key = (data[offset] << 16) + (data[offset + 1] << 8) + data[offset + 2]
      sourceColorCounts.set(key, (sourceColorCounts.get(key) || 0) + 1)
    }

    if (pixelIndex > 0 && pixelIndex % 65536 === 0) {
      reportProgress(onProgress, 'counting', pixelIndex, totalPixels, '正在统计原图像素颜色')
      await yieldToMainThread()
    }
  }

  const uniqueColorEntries = [...sourceColorCounts.entries()]
  const nearestMatcher = buildNearestColorMatcher(palette, distanceMethod)
  const sourceToProjected = new Map<number, StandardColor>()
  const projectedColorCounts = new Map<string, { color: StandardColor; count: number }>()

  reportProgress(onProgress, 'matching', 0, uniqueColorEntries.length, '正在把原图颜色映射到标准色空间')

  for (const [index, [sourceKey, count]] of uniqueColorEntries.entries()) {
    const sourceColor = fromRgbIntKey(sourceKey)
    const matchedColor = nearestMatcher(sourceColor)
    sourceToProjected.set(sourceKey, matchedColor)

    const existing = projectedColorCounts.get(matchedColor.hex)
    if (existing) {
      existing.count += count
    } else {
      projectedColorCounts.set(matchedColor.hex, {
        color: matchedColor,
        count,
      })
    }

    if (index > 0 && index % 512 === 0) {
      reportProgress(onProgress, 'matching', index, uniqueColorEntries.length, '正在把原图颜色映射到标准色空间')
      await yieldToMainThread()
    }
  }

  reportProgress(onProgress, 'rendering', 0, totalPixels, '正在生成对齐后的颜色投影图')

  for (let pixelIndex = 0; pixelIndex < totalPixels; pixelIndex += 1) {
    const offset = pixelIndex * 4
    const alpha = data[offset + 3]
    if (alpha !== 0) {
      const sourceKey = (data[offset] << 16) + (data[offset + 1] << 8) + data[offset + 2]
      const matchedColor = sourceToProjected.get(sourceKey)
      if (matchedColor) {
        data[offset] = matchedColor.r
        data[offset + 1] = matchedColor.g
        data[offset + 2] = matchedColor.b
      }
    }

    if (pixelIndex > 0 && pixelIndex % 65536 === 0) {
      reportProgress(onProgress, 'rendering', pixelIndex, totalPixels, '正在生成对齐后的颜色投影图')
      await yieldToMainThread()
    }
  }

  context.putImageData(imageData, 0, 0)
  const projectedBlob = await canvasToBlob(canvas)

  const rows = [...projectedColorCounts.values()]
    .sort((left, right) => right.count - left.count)
    .map((entry, index) => ({
      rank: index + 1,
      color: entry.color,
      count: entry.count,
      percent: opaquePixels > 0 ? entry.count / opaquePixels : 0,
    }))

  reportProgress(onProgress, 'rendering', totalPixels, totalPixels, '颜色分布分析完成')

  return {
    width,
    height,
    totalPixels,
    opaquePixels,
    transparentPixels,
    uniqueSourceColorCount: sourceColorCounts.size,
    uniqueProjectedColorCount: projectedColorCounts.size,
    paletteSize: palette.length,
    rows,
    projectedBlob,
  }
}

export function getTopCoveragePercent(rows: ColorDistributionRow[], limit: number): number {
  return rows.slice(0, limit).reduce((sum, row) => sum + row.percent, 0)
}
