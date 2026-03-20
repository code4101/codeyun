import { COLOR_LIST0, COLOR_LIST1, COLOR_LIST2 } from '@/utils/colorCatalogSource'

export type ColorGroupId = 'core-zh' | 'extended-zh' | 'english-expanded'
export type ColorRange = 0 | 1 | 2
export type DistanceMethod = 'cie76' | 'compuphase' | 'euclidean'

export interface RgbColor {
  r: number
  g: number
  b: number
}

export interface ColorGroupDefinition {
  id: ColorGroupId
  label: string
  shortLabel: string
  description: string
  range: ColorRange
}

export interface DistanceMethodDefinition {
  value: DistanceMethod
  label: string
  description: string
}

interface RawColorEntry {
  hex: string
  zhName: string
  enName: string
  groupId: ColorGroupId
}

export interface StandardColor extends RgbColor {
  hex: string
  groupId?: ColorGroupId
  zhNames: string[]
  enNames: string[]
  names: string[]
  displayName: string
}

interface ColorCatalog {
  colors: StandardColor[]
  nameToHex: Map<string, string>
  hexToNames: Map<string, string>
}

interface LabColor {
  l: number
  a: number
  b: number
}

const MAX_RGB_CHANNEL = 255
const VBA_COLOR_SPACE = 256 ** 3
const XYZ_REFERENCE_WHITE = {
  x: 95.047,
  y: 100,
  z: 108.883,
}

export const COLOR_GROUPS: ColorGroupDefinition[] = [
  {
    id: 'core-zh',
    label: '基础中文色卡',
    shortLabel: '基础中文',
    description: '对应 rgbfmt.py 的 _COLOR_LIST0，包含常用中文名称和基础英文名。',
    range: 0,
  },
  {
    id: 'extended-zh',
    label: '扩展中文色卡',
    shortLabel: '扩展中文',
    description: '对应 _COLOR_LIST1，包含补充中文色与几组 Git 视觉色。',
    range: 1,
  },
  {
    id: 'english-expanded',
    label: '英文扩展色卡',
    shortLabel: '英文扩展',
    description: '对应 _COLOR_LIST2，收录大量英文标准色和别名。',
    range: 2,
  },
]

export const DISTANCE_METHODS: DistanceMethodDefinition[] = [
  {
    value: 'cie76',
    label: 'CIE76 (Lab)',
    description: '先转 Lab 颜色空间再计算距离，更接近视觉分布，适合做颜色投影。',
  },
  {
    value: 'compuphase',
    label: 'Compuphase',
    description: 'rgbfmt.py 里已有的加权 RGB 距离，对绿色更敏感。',
  },
  {
    value: 'euclidean',
    label: 'RGB 欧氏',
    description: '最直观也最快，直接在 RGB 空间算欧氏距离。',
  },
]

const RAW_COLOR_SOURCES: Record<ColorGroupId, string> = {
  'core-zh': COLOR_LIST0,
  'extended-zh': COLOR_LIST1,
  'english-expanded': COLOR_LIST2,
}

const GROUP_ORDER: ColorGroupId[] = COLOR_GROUPS.map(group => group.id)

function clampChannel(value: number): number {
  if (!Number.isFinite(value)) return 0
  return Math.max(0, Math.min(MAX_RGB_CHANNEL, Math.round(value)))
}

function srgbToLinear(value: number): number {
  const normalized = value / 255
  if (normalized <= 0.04045) {
    return normalized / 12.92
  }
  return ((normalized + 0.055) / 1.055) ** 2.4
}

function rgbToLab(color: RgbColor): LabColor {
  const r = srgbToLinear(color.r)
  const g = srgbToLinear(color.g)
  const b = srgbToLinear(color.b)

  const x = (r * 0.4124 + g * 0.3576 + b * 0.1805) * 100
  const y = (r * 0.2126 + g * 0.7152 + b * 0.0722) * 100
  const z = (r * 0.0193 + g * 0.1192 + b * 0.9505) * 100

  const transform = (value: number): number => {
    if (value > 0.008856) {
      return value ** (1 / 3)
    }
    return 7.787 * value + 16 / 116
  }

  const fx = transform(x / XYZ_REFERENCE_WHITE.x)
  const fy = transform(y / XYZ_REFERENCE_WHITE.y)
  const fz = transform(z / XYZ_REFERENCE_WHITE.z)

  return {
    l: 116 * fy - 16,
    a: 500 * (fx - fy),
    b: 200 * (fy - fz),
  }
}

function normalizeHex(hex: string): string {
  const match = hex.match(/([0-9a-fA-F]{6})/)
  if (!match) {
    throw new Error(`16进制颜色格式有误: ${hex}`)
  }
  return `#${match[1].toUpperCase()}`
}

function toHexWithoutHash(hex: string): string {
  return normalizeHex(hex).slice(1)
}

function parseColorList(raw: string, groupId: ColorGroupId): RawColorEntry[] {
  return raw
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [hex = '', zhName = '', enName = ''] = line.split(',')
      return {
        hex: normalizeHex(hex),
        zhName: zhName.trim(),
        enName: enName.trim(),
        groupId,
      }
    })
}

function appendUnique(target: string[], value: string, caseInsensitive = false): void {
  if (!value) return

  const exists = caseInsensitive
    ? target.some(item => item.toLowerCase() === value.toLowerCase())
    : target.includes(value)

  if (!exists) {
    target.push(value)
  }
}

function buildPalette(entries: RawColorEntry[], groupId?: ColorGroupId): StandardColor[] {
  const palette = new Map<string, StandardColor>()

  for (const entry of entries) {
    const existing = palette.get(entry.hex)
    if (!existing) {
      const { r, g, b } = fromHex(entry.hex)
      const nextColor: StandardColor = {
        hex: entry.hex,
        r,
        g,
        b,
        groupId,
        zhNames: [],
        enNames: [],
        names: [],
        displayName: entry.zhName || entry.enName || entry.hex,
      }
      palette.set(entry.hex, nextColor)
    }

    const color = palette.get(entry.hex)!
    appendUnique(color.zhNames, entry.zhName)
    appendUnique(color.enNames, entry.enName, true)
  }

  for (const color of palette.values()) {
    color.names = [...color.zhNames, ...color.enNames]
    color.displayName = color.zhNames[0] || color.enNames[0] || color.hex
  }

  return [...palette.values()]
}

const GROUP_ENTRIES: Record<ColorGroupId, RawColorEntry[]> = {
  'core-zh': parseColorList(RAW_COLOR_SOURCES['core-zh'], 'core-zh'),
  'extended-zh': parseColorList(RAW_COLOR_SOURCES['extended-zh'], 'extended-zh'),
  'english-expanded': parseColorList(RAW_COLOR_SOURCES['english-expanded'], 'english-expanded'),
}

const GROUP_PALETTES: Record<ColorGroupId, StandardColor[]> = {
  'core-zh': buildPalette(GROUP_ENTRIES['core-zh'], 'core-zh'),
  'extended-zh': buildPalette(GROUP_ENTRIES['extended-zh'], 'extended-zh'),
  'english-expanded': buildPalette(GROUP_ENTRIES['english-expanded'], 'english-expanded'),
}

const RANGE_CATALOGS = new Map<ColorRange, ColorCatalog>()

function createCatalog(range: ColorRange): ColorCatalog {
  const mergedEntries = GROUP_ORDER
    .slice(0, range + 1)
    .flatMap(groupId => GROUP_ENTRIES[groupId])

  const colors = buildPalette(mergedEntries)
  const nameToHex = new Map<string, string>()
  const hexToNames = new Map<string, string>()

  for (const color of colors) {
    hexToNames.set(color.hex, color.names.join(','))

    for (const name of color.zhNames) {
      nameToHex.set(name, color.hex)
    }

    for (const name of color.enNames) {
      nameToHex.set(name, color.hex)
      nameToHex.set(name.toLowerCase(), color.hex)
    }
  }

  return {
    colors,
    nameToHex,
    hexToNames,
  }
}

function getCatalog(range: ColorRange = 2): ColorCatalog {
  if (!RANGE_CATALOGS.has(range)) {
    RANGE_CATALOGS.set(range, createCatalog(range))
  }

  return RANGE_CATALOGS.get(range)!
}

export function createRgbColor(r = 0, g = 0, b = 0): RgbColor {
  return {
    r: clampChannel(r),
    g: clampChannel(g),
    b: clampChannel(b),
  }
}

export function fromRgbInt255(r = 0, g = 0, b = 0): RgbColor {
  return createRgbColor(r, g, b)
}

export function toTuple(color: RgbColor): [number, number, number] {
  return [color.r, color.g, color.b]
}

export function toRgbIntKey(color: RgbColor): number {
  return (color.r << 16) + (color.g << 8) + color.b
}

export function fromRgbIntKey(key: number): RgbColor {
  return {
    r: (key >> 16) & 255,
    g: (key >> 8) & 255,
    b: key & 255,
  }
}

export function fromHex(hex: string): RgbColor {
  const normalized = normalizeHex(hex)
  return {
    r: Number.parseInt(normalized.slice(1, 3), 16),
    g: Number.parseInt(normalized.slice(3, 5), 16),
    b: Number.parseInt(normalized.slice(5, 7), 16),
  }
}

export function toHex(color: RgbColor, lower = false): string {
  const hex = `#${clampChannel(color.r).toString(16).padStart(2, '0')}${clampChannel(color.g).toString(16).padStart(2, '0')}${clampChannel(color.b).toString(16).padStart(2, '0')}`
  return lower ? hex.toLowerCase() : hex.toUpperCase()
}

export function fromPercentage(r: number, g: number, b: number): RgbColor {
  return createRgbColor(r * MAX_RGB_CHANNEL, g * MAX_RGB_CHANNEL, b * MAX_RGB_CHANNEL)
}

export function toPercentage(color: RgbColor): [number, number, number] {
  return [color.r / MAX_RGB_CHANNEL, color.g / MAX_RGB_CHANNEL, color.b / MAX_RGB_CHANNEL]
}

export function fromVbaValue(value: number): RgbColor {
  let normalized = Math.trunc(value)
  if (normalized < 0) {
    normalized += VBA_COLOR_SPACE
  }

  return {
    r: normalized % 256,
    g: Math.floor(normalized / 256) % 256,
    b: Math.floor(normalized / 65536),
  }
}

export function toVbaValue(color: RgbColor, negative = false): number {
  let value = color.r + color.g * 256 + color.b * 65536
  if (negative) {
    value -= VBA_COLOR_SPACE
  }
  return value
}

export function mixColors(
  base: RgbColor,
  others: RgbColor | RgbColor[],
  ratios: number | number[] = 1,
): RgbColor {
  const otherColors = Array.isArray(others) ? [...others] : [others]
  const otherRatios = Array.isArray(ratios) ? [...ratios] : [ratios]

  otherColors.push(base)
  otherRatios.push(1)

  while (otherRatios.length < otherColors.length) {
    otherRatios.push(1)
  }

  const totalRatio = otherRatios.reduce((sum, value) => sum + value, 0)
  const mixed = otherColors.reduce(
    (acc, color, index) => {
      const ratio = otherRatios[index]
      acc.r += color.r * ratio
      acc.g += color.g * ratio
      acc.b += color.b * ratio
      return acc
    },
    { r: 0, g: 0, b: 0 },
  )

  return createRgbColor(mixed.r / totalRatio, mixed.g / totalRatio, mixed.b / totalRatio)
}

export function lightenColor(color: RgbColor, ratio = 1): RgbColor {
  return mixColors(color, createRgbColor(255, 255, 255), ratio)
}

export function distance(colorA: RgbColor, colorB: RgbColor): number {
  const rMean = Math.floor((colorA.r + colorB.r) / 2)
  const r = colorA.r - colorB.r
  const g = colorA.g - colorB.g
  const b = colorA.b - colorB.b

  return Math.sqrt(
    (((512 + rMean) * r * r) >> 8)
    + 4 * g * g
    + (((767 - rMean) * b * b) >> 8),
  )
}

export function euclideanDistance(colorA: RgbColor, colorB: RgbColor): number {
  return Math.sqrt(
    (colorA.r - colorB.r) ** 2
    + (colorA.g - colorB.g) ** 2
    + (colorA.b - colorB.b) ** 2,
  )
}

export function cie76Distance(colorA: RgbColor, colorB: RgbColor): number {
  const labA = rgbToLab(colorA)
  const labB = rgbToLab(colorB)
  return Math.sqrt(
    (labA.l - labB.l) ** 2
    + (labA.a - labB.a) ** 2
    + (labA.b - labB.b) ** 2,
  )
}

export function getColorDistance(colorA: RgbColor, colorB: RgbColor, method: DistanceMethod = 'compuphase'): number {
  if (method === 'euclidean') {
    return euclideanDistance(colorA, colorB)
  }

  if (method === 'cie76') {
    return cie76Distance(colorA, colorB)
  }

  return distance(colorA, colorB)
}

export function getGroupPalette(groupId: ColorGroupId): StandardColor[] {
  return GROUP_PALETTES[groupId]
}

export function getPaletteForGroups(groupIds: ColorGroupId[]): StandardColor[] {
  const normalizedGroupIds = GROUP_ORDER.filter(groupId => groupIds.includes(groupId))
  const mergedEntries = normalizedGroupIds.flatMap(groupId => GROUP_ENTRIES[groupId])
  return buildPalette(mergedEntries)
}

export function getStandardColors(range: ColorRange = 2): StandardColor[] {
  return getCatalog(range).colors
}

export function findExactStandardColorByHex(hex: string, range: ColorRange = 2): StandardColor | undefined {
  const target = normalizeHex(hex)
  return getStandardColors(range).find(color => color.hex === target)
}

export function findColorByName(name: string, range: ColorRange = 2): StandardColor | undefined {
  const trimmed = name.trim()
  if (!trimmed) return undefined

  const { nameToHex } = getCatalog(range)
  const hex = nameToHex.get(trimmed) || nameToHex.get(trimmed.toLowerCase())
  return hex ? findExactStandardColorByHex(hex, range) : undefined
}

export function findSimilarStandardColor(
  color: RgbColor,
  options: { range?: ColorRange; preciseMode?: boolean; method?: DistanceMethod } = {},
): StandardColor {
  const { range = 2, preciseMode = false, method } = options
  const colors = getStandardColors(range)
  const resolvedMethod = method ?? (preciseMode ? 'compuphase' : 'euclidean')

  if (!colors.length) {
    throw new Error('没有可用的标准颜色集合')
  }

  if (resolvedMethod === 'euclidean') {
    let nearest = colors[0]
    let nearestDistance = Number.POSITIVE_INFINITY

    for (const candidate of colors) {
      const currentDistance
        = (candidate.r - color.r) ** 2
        + (candidate.g - color.g) ** 2
        + (candidate.b - color.b) ** 2

      if (currentDistance < nearestDistance) {
        nearestDistance = currentDistance
        nearest = candidate
      }
    }

    return nearest
  }

  if (resolvedMethod === 'cie76') {
    const targetLab = rgbToLab(color)
    let nearest = colors[0]
    let nearestDistance = Number.POSITIVE_INFINITY

    for (const candidate of colors) {
      const candidateLab = rgbToLab(candidate)
      const currentDistance
        = (candidateLab.l - targetLab.l) ** 2
        + (candidateLab.a - targetLab.a) ** 2
        + (candidateLab.b - targetLab.b) ** 2

      if (currentDistance < nearestDistance) {
        nearestDistance = currentDistance
        nearest = candidate
      }
    }

    return nearest
  }

  let nearest = colors[0]
  let nearestDistance = Number.POSITIVE_INFINITY

  for (const candidate of colors) {
    const currentDistance = distance(color, candidate)
    if (currentDistance < nearestDistance) {
      nearestDistance = currentDistance
      nearest = candidate
    }
  }

  return nearest
}

export function getRelativeColorDescription(
  color: RgbColor,
  relativeColor?: RgbColor,
  options: { range?: ColorRange; preciseMode?: boolean; method?: DistanceMethod } = {},
): string {
  const { range = 2, preciseMode = false, method } = options
  const standardColor = relativeColor ?? findSimilarStandardColor(color, { range, preciseMode, method })
  const desc = getCatalog(range).hexToNames.get(toHex(standardColor)) || ''
  const deltaR = color.r - standardColor.r
  const deltaG = color.g - standardColor.g
  const deltaB = color.b - standardColor.b

  const formatDelta = (value: number): string => (value ? `${value > 0 ? '+' : ''}${value}` : '')

  return `(${standardColor.r}${formatDelta(deltaR)}, ${standardColor.g}${formatDelta(deltaG)}, ${standardColor.b}${formatDelta(deltaB)}) ${desc}`.trim()
}

export function parseColorInput(input: string, range: ColorRange = 2): RgbColor | undefined {
  const text = input.trim()
  if (!text) return undefined

  const matchedHex = text.match(/#?[0-9a-fA-F]{6}/)
  if (matchedHex) {
    return fromHex(matchedHex[0])
  }

  const rgbMatch = text.match(/^rgb?\s*\(?\s*(\d{1,3})\s*[,，]\s*(\d{1,3})\s*[,，]\s*(\d{1,3})\s*\)?$/i)
  if (rgbMatch) {
    return createRgbColor(
      Number.parseInt(rgbMatch[1], 10),
      Number.parseInt(rgbMatch[2], 10),
      Number.parseInt(rgbMatch[3], 10),
    )
  }

  const vbaMatch = text.match(/^(?:vba[:\s]*)?(-?\d+)$/i)
  if (vbaMatch) {
    return fromVbaValue(Number.parseInt(vbaMatch[1], 10))
  }

  const standardColor = findColorByName(text, range)
  return standardColor ? createRgbColor(standardColor.r, standardColor.g, standardColor.b) : undefined
}

export function getReadableTextColor(color: RgbColor): '#111827' | '#FFFFFF' {
  const brightness = (color.r * 299 + color.g * 587 + color.b * 114) / 1000
  return brightness > 150 ? '#111827' : '#FFFFFF'
}

export function getColorGroupByRange(range: ColorRange): ColorGroupDefinition {
  return COLOR_GROUPS[range]
}

export function getColorMatchesByHex(hex: string): Record<ColorGroupId, StandardColor | undefined> {
  const normalized = toHexWithoutHash(hex)

  return {
    'core-zh': GROUP_PALETTES['core-zh'].find(color => color.hex.slice(1) === normalized),
    'extended-zh': GROUP_PALETTES['extended-zh'].find(color => color.hex.slice(1) === normalized),
    'english-expanded': GROUP_PALETTES['english-expanded'].find(color => color.hex.slice(1) === normalized),
  }
}

export function buildNearestColorMatcher(
  palette: StandardColor[],
  method: DistanceMethod = 'cie76',
): (color: RgbColor) => StandardColor {
  if (!palette.length) {
    throw new Error('没有可用的标准颜色集合')
  }

  const cache = new Map<number, StandardColor>()
  const paletteLabs = method === 'cie76' ? palette.map(color => rgbToLab(color)) : []

  return (color: RgbColor) => {
    const cacheKey = toRgbIntKey(color)
    const cached = cache.get(cacheKey)
    if (cached) {
      return cached
    }

    let nearest = palette[0]
    let nearestDistance = Number.POSITIVE_INFINITY

    if (method === 'euclidean') {
      for (const candidate of palette) {
        const currentDistance
          = (candidate.r - color.r) ** 2
          + (candidate.g - color.g) ** 2
          + (candidate.b - color.b) ** 2

        if (currentDistance < nearestDistance) {
          nearestDistance = currentDistance
          nearest = candidate
        }
      }
    } else if (method === 'cie76') {
      const targetLab = rgbToLab(color)

      for (const [index, candidate] of palette.entries()) {
        const candidateLab = paletteLabs[index]
        const currentDistance
          = (candidateLab.l - targetLab.l) ** 2
          + (candidateLab.a - targetLab.a) ** 2
          + (candidateLab.b - targetLab.b) ** 2

        if (currentDistance < nearestDistance) {
          nearestDistance = currentDistance
          nearest = candidate
        }
      }
    } else {
      for (const candidate of palette) {
        const currentDistance = distance(color, candidate)
        if (currentDistance < nearestDistance) {
          nearestDistance = currentDistance
          nearest = candidate
        }
      }
    }

    cache.set(cacheKey, nearest)
    return nearest
  }
}
