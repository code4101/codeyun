/**
 * 长度的「比例比喻」工具 —— 这一层只是把通用引擎绑到长度量纲上。
 * 引擎在 @/utils/scale，数据在 @/utils/dimensions/length。
 */
import {
  buildPools,
  convert,
  findChain as findChainIn,
  findProportions as findProportionsIn,
  findUnit,
  formatSignificant,
  formatSci,
  formatWithUnits,
  labelOf,
  nearestAnchor as nearestAnchorIn,
  resolveInput,
  scaleOptions,
  solveProportion as solveProportionIn,
  toSuperscript,
  unitOptions,
  unitsByScale,
  visibleOptions,
  type ChainGroup,
  type FindProportionsOptions,
  type NamedValue,
  type ProportionSuggestion,
  type ProportionTermId,
  type ProportionTerms,
  type ScaleOption,
} from './scale.ts'

import { EARTH_EQUATOR_LENGTH, lengthSystem } from './dimensions/length.ts'

export {
  EARTH_EQUATOR_LENGTH,
  LENGTH_ANCHORS,
  LENGTH_ANCHOR_CATEGORIES,
  LENGTH_RULERS,
  LENGTH_UNITS,
} from './dimensions/length.ts'

export type {
  ChainGroup,
  NamedValue,
  ProportionSuggestion,
  ProportionTermId,
  ProportionTerms,
  ScaleAnchor,
  ScaleCategory,
  ScaleUnit,
} from './scale.ts'

/** c、d 的下限：比 1 毫米还小的东西，光看是看不见的，讲不出画面。 */
export const EVERYDAY_MIN_METERS = lengthSystem.visible.min

/** b 默认就在这一档里挑：从一粒米到一段城市距离。 */
export const DEFAULT_BRIDGE_MIN_METERS = lengthSystem.bridge.min
export const DEFAULT_BRIDGE_MAX_METERS = lengthSystem.bridge.max

/** 全部长度单位，按尺度升序——a 的单位下拉直接用这个。 */
export const UNITS_BY_SCALE = unitsByScale(lengthSystem)

/** a 的单位下拉：每个单位一条。 */
export const UNIT_OPTIONS: ScaleOption[] = unitOptions(lengthSystem)

/** b 可以挑的：任何有名字的东西，外加单位尺。 */
export const BRIDGE_OPTIONS: ScaleOption[] = scaleOptions(lengthSystem, [
  ...lengthSystem.anchors.filter((anchor) => !anchor.abstract).map((anchor) => ({ value: anchor.value, name: anchor.name })),
  ...lengthSystem.rulers,
])

/** c、d 可以挑的：看得见、在地球范围内的东西。 */
export const RIGHT_OPTIONS: ScaleOption[] = visibleOptions(lengthSystem)

export const LENGTH_ANCHOR_CATEGORIES_LEGACY = lengthSystem.categories

export const formatLength = (value: number, digits = 3): string => formatWithUnits(lengthSystem, value, digits)
export const lengthLabel = (item: NamedValue): string => labelOf(lengthSystem, item)
export { formatSignificant, formatSci, toSuperscript }

export function findLengthUnit(idOrSymbolOrName: string) {
  return findUnit(lengthSystem, idOrSymbolOrName)
}

export function convertLength(value: number, fromUnitId: string, toUnitId: string): number {
  return convert(lengthSystem, value, fromUnitId, toUnitId)
}

export function toMeters(value: number, unitId: string): number {
  return convertLength(value, unitId, 'm')
}

export interface ParsedLength {
  /** 换算到基准单位（米）之后的数值 */
  value: number
  unit: ReturnType<typeof findLengthUnit>
  name: string
}

export function parseLength(input: string): ParsedLength | null {
  const resolved = resolveInput(lengthSystem, input)
  if (!resolved) return null
  const numeric = input.trim().match(/^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*(.*)$/)
  if (!numeric) return null
  const unit = findLengthUnit(numeric[2].trim())
  if (!unit) return null
  return { value: resolved.value, unit, name: resolved.name }
}

export function resolveLengthInput(input: string): NamedValue | null {
  return resolveInput(lengthSystem, input)
}

export function nearestAnchor(value: number, anchors?: NamedValue[]) {
  if (!anchors) return nearestAnchorIn(lengthSystem, value)
  return [...anchors]
    .sort((left, right) => Math.abs(Math.log10(left.value / value)) - Math.abs(Math.log10(right.value / value)))[0]
}

export function solveProportion(terms: Partial<ProportionTerms>, unknown: ProportionTermId): number | null {
  return solveProportionIn(terms, unknown)
}

export function findProportions(target: number, options: FindProportionsOptions = {}): ProportionSuggestion[] {
  return findProportionsIn(lengthSystem, target, options)
}

export function findChain(target: number, options: { b: NamedValue; maxGroups?: number }) {
  return findChainIn(lengthSystem, target, options)
}

/** 让「一条分式能装下多少」这类内部参数也能被外面看见。 */
export const LENGTH_SYSTEM = lengthSystem
export { buildPools }
export type { ChainGroup as LengthChainGroup }
