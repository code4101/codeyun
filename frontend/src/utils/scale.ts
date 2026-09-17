/**
 * 与量纲无关的「比例比喻」引擎。
 *
 * 一个量纲要提供三张表（单位、常识锚点、单位尺）和两条边界，引擎负责：
 *   · 把 a 换成一对比值相同的实物： a : b = c : d（必要时右边拆成几组相乘）
 *   · 由 b、c、d 反推左边那个精确微调值，保证整条式子数学上成立
 * 长度、时间、质量……都走同一套代码，差别只在那三张表。
 */

export interface NamedValue {
  value: number
  name: string
}

export interface ScaleUnit {
  id: string
  name: string
  symbol: string
  /** 1 个该单位等于多少基准单位（长度的基准是米、时间的基准是秒） */
  factor: number
  aliases?: string[]
}

export interface ScaleAnchor {
  value: number
  name: string
  category: string
  note?: string
  source?: string
  /** 同一个东西的不同量（半径/直径）共用一个 id，避免自我类比 */
  object?: string
  /** 1 = 伸手可及或常识；2 = 需要一点科普；3 = 专业尺度 */
  familiarity?: 1 | 2 | 3
  /** 熟悉到能当参照物 */
  everyday?: boolean
  /** 只是理论尺度，不参与配对 */
  abstract?: boolean
}

export interface ScaleCategory {
  id: string
  title: string
  color: string
}

export interface ScaleOption {
  /** 选项值：参照物的名字 */
  name: string
  /** 下拉里显示的文字，带上它对应的精确数值 */
  label: string
}

export interface ScaleSystem {
  id: string
  title: string
  /** 基准单位名，用于提示 */
  baseUnit: string
  units: ScaleUnit[]
  anchors: ScaleAnchor[]
  /** 单位尺：把「1 秒」「1 公里」这类单位本身也当成一根参照物 */
  rulers: NamedValue[]
  /** c、d 必须落在这个区间（看得见、不出地球） */
  visible: { min: number; max: number }
  /** b 默认在这个区间里挑 */
  bridge: { min: number; max: number }
  /** 显示时优先用哪些单位 */
  displayUnits: string[]
  /** 打开页面时默认选中的单位 id */
  defaultUnit?: string
  categories: ScaleCategory[]
}

const SUPERSCRIPT_DIGITS = '⁰¹²³⁴⁵⁶⁷⁸⁹'
const SUPERSCRIPT_MINUS = '⁻'

export function toSuperscript(value: number): string {
  const sign = value < 0 ? SUPERSCRIPT_MINUS : ''
  return sign + Math.abs(value).toString().split('').map((digit) => SUPERSCRIPT_DIGITS[Number(digit)]).join('')
}

export function formatSignificant(value: number, digits = 3): string {
  if (!Number.isFinite(value)) return String(value)
  if (value === 0) return '0'
  const text = value.toPrecision(digits)
  return text.includes('.') ? text.replace(/\.?0+$/, '') : text
}

export function formatSci(value: number, digits = 2): string {
  if (!Number.isFinite(value)) return String(value)
  if (value === 0) return '0'
  const exponent = Math.floor(Math.log10(Math.abs(value)))
  if (exponent >= -3 && exponent <= 4) return formatSignificant(value, digits + Math.abs(exponent))
  return `${(value / 10 ** exponent).toFixed(digits)}×10${toSuperscript(exponent)}`
}

function formatAdaptive(value: number, digits: number): string {
  const absolute = Math.abs(value)
  if (absolute === 0) return '0'
  if (absolute >= 1e6 || absolute < 1e-3) return formatSci(value, digits)
  const magnitude = Math.floor(Math.log10(absolute))
  return formatSignificant(value, Math.min(8, Math.max(digits, magnitude + 1)))
}

/** 自适应挑一个显示单位，得到「4.5 m」这种可读形式。 */
export function formatWithUnits(system: ScaleSystem, value: number, digits = 3): string {
  if (!Number.isFinite(value)) return String(value)
  const units = system.displayUnits
    .map((id) => findUnit(system, id))
    .filter((unit): unit is ScaleUnit => unit !== undefined)
    .sort((left, right) => left.factor - right.factor)
  if (value === 0) return `0 ${units[0]?.symbol ?? system.baseUnit}`
  if (units.length === 0) return formatSci(value, digits)

  const absolute = Math.abs(value)
  let chosen = units[0]
  for (const unit of units) {
    if (unit.factor <= absolute) chosen = unit
  }
  return `${formatAdaptive(value / chosen.factor, digits)} ${chosen.symbol}`
}

/** 参照物的显示名：单位尺只写名字，实物后面补上它精确的数值。 */
export function labelOf(system: ScaleSystem, item: NamedValue): string {
  const isRuler = system.rulers.some((ruler) => ruler.name === item.name)
  return isRuler ? item.name : `${item.name}（${formatWithUnits(system, item.value)}）`
}

export function findUnit(system: ScaleSystem, input: string): ScaleUnit | undefined {
  const trimmed = input.trim()
  const lower = trimmed.toLowerCase()
  return system.units.find((unit) => {
    // 符号必须大小写精确：SI 前缀里 Mm（兆）和 mm（毫）是两回事
    if (unit.symbol === trimmed) return true
    if (unit.id === trimmed) return true
    if (unit.name.toLowerCase() === lower) return true
    return (unit.aliases ?? []).some((alias) => alias.toLowerCase() === lower)
  })
}

export function unitsByScale(system: ScaleSystem): ScaleUnit[] {
  return [...system.units].sort((left, right) => left.factor - right.factor)
}

export function convert(system: ScaleSystem, value: number, fromId: string, toId: string): number {
  const from = findUnit(system, fromId)
  const to = findUnit(system, toId)
  if (!from) throw new Error(`未知单位：${fromId}`)
  if (!to) throw new Error(`未知单位：${toId}`)
  return (value * from.factor) / to.factor
}

export function scaleOptions(system: ScaleSystem, items: NamedValue[]): ScaleOption[] {
  return [...items]
    .sort((left, right) => left.value - right.value)
    .map((item) => ({ name: item.name, label: labelOf(system, item) }))
}

export function unitOptions(system: ScaleSystem): ScaleOption[] {
  return unitsByScale(system).map((unit) => ({ name: unit.id, label: `${unit.name} ${unit.symbol}` }))
}

export function namedAnchors(system: ScaleSystem): NamedValue[] {
  return system.anchors.filter((anchor) => !anchor.abstract).map((anchor) => ({ value: anchor.value, name: anchor.name }))
}

/** 看得见的日常参照物：既要 ≥ 下限（太小看不见），也要 ≤ 上限（不出地球）。 */
export function everydayAnchors(system: ScaleSystem): NamedValue[] {
  return system.anchors
    .filter((anchor) => anchor.everyday
      && !anchor.abstract
      && anchor.value >= system.visible.min
      && anchor.value <= system.visible.max)
    .map((anchor) => ({ value: anchor.value, name: anchor.name }))
}

/** 看得见的单位尺。 */
function visibleRulers(system: ScaleSystem): NamedValue[] {
  return system.rulers.filter((ruler) => ruler.value >= system.visible.min && ruler.value <= system.visible.max)
}

/** b 可以挑的：任何有名字的东西，外加单位尺。 */
export function bridgeOptions(system: ScaleSystem): ScaleOption[] {
  return scaleOptions(system, [...namedAnchors(system), ...system.rulers])
}

/** c、d 可以挑的：看得见、在地球范围内的东西。 */
export function visibleOptions(system: ScaleSystem): ScaleOption[] {
  return scaleOptions(system, [...everydayAnchors(system), ...visibleRulers(system)])
}

/** 输入可以是「1.5 秒」「3 分钟」、锚点名字（如「一次心跳」），或带系数的「2 × 一天」。 */
export function resolveInput(system: ScaleSystem, input: string): NamedValue | null {
  const trimmed = input.trim()
  if (!trimmed) return null

  const prefixed = trimmed.match(/^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*[×xX*]\s*(.+)$/)
  if (prefixed) {
    const coefficient = Number(prefixed[1])
    const rest = resolveInput(system, prefixed[2])
    if (rest && Number.isFinite(coefficient) && coefficient > 0) {
      return {
        value: coefficient * rest.value,
        name: `${formatSignificant(coefficient, 3)} × ${rest.name}`,
      }
    }
  }

  const anchor = system.anchors.find((item) => item.name === trimmed)
  if (anchor) return { value: anchor.value, name: anchor.name }
  const ruler = system.rulers.find((item) => item.name === trimmed)
  if (ruler) return { value: ruler.value, name: ruler.name }

  const match = trimmed.match(/^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*(.*)$/)
  if (!match) return null
  const value = Number(match[1])
  const unit = findUnit(system, match[2].trim())
  if (!Number.isFinite(value) || !unit) return null
  return { value: value * unit.factor, name: `${formatSignificant(value, 6)} ${unit.symbol}` }
}

export function nearestAnchor(system: ScaleSystem, value: number): ScaleAnchor | undefined {
  const anchors = system.anchors
  if (!Number.isFinite(value) || value <= 0 || anchors.length === 0) return anchors[0]
  let best = anchors[0]
  let bestDistance = Number.POSITIVE_INFINITY
  for (const anchor of anchors) {
    const distance = Math.abs(Math.log10(anchor.value) - Math.log10(value))
    if (distance < bestDistance) {
      best = anchor
      bestDistance = distance
    }
  }
  return best
}

export type ProportionTermId = 'a' | 'b' | 'c' | 'd'

export interface ProportionTerms {
  a: number
  b: number
  c: number
  d: number
}

/**
 * a : b = c : d 的四种解（a·d = b·c）。
 * 给定三个已知量，解出第四个 —— 这一步与量纲完全无关。
 */
export function solveProportion(terms: Partial<ProportionTerms>, unknown: ProportionTermId): number | null {
  const { a, b, c, d } = terms
  const known = [a, b, c, d].filter((value) => typeof value === 'number' && Number.isFinite(value) && value !== 0)
  if (known.length < 3) return null

  switch (unknown) {
    case 'a':
      return b !== undefined && c !== undefined && d !== undefined && d !== 0 ? (b * c) / d : null
    case 'b':
      return a !== undefined && c !== undefined && d !== undefined && c !== 0 ? (a * d) / c : null
    case 'c':
      return a !== undefined && b !== undefined && d !== undefined && b !== 0 ? (a * d) / b : null
    case 'd':
      return a !== undefined && b !== undefined && c !== undefined && a !== 0 ? (b * c) / a : null
    default:
      return null
  }
}

export interface ProportionSuggestion {
  terms: { a: NamedValue; b: NamedValue; c: NamedValue; d: NamedValue }
  /** 左边那个自动微调的数：乘上 a，这条比例式就精确成立 */
  factor: number
  /** 修正系数，恒 ≥ 1；1 表示这一对参照物本身就精确对得上 */
  coefficient: number
  coefficientSide: 'c' | 'd'
  span: number
  error: number
  text: string
}

export interface FindProportionsOptions {
  limit?: number
  maxError?: number
  minSpan?: number
  maxSpan?: number
  targetName?: string
  /** 用户手动钉住的项；钉住的不参与搜索，其余自动重配 */
  fix?: { b?: NamedValue; c?: NamedValue; d?: NamedValue }
}

interface PoolEntry extends NamedValue {
  object: string
  everyday: boolean
}

const SAME_VALUE_TOLERANCE = 1e-3
const SPAN_SWEET_SPOT = 4.5
const COEFFICIENT_BASES = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]
const COEFFICIENTS: number[] = [...COEFFICIENT_BASES].sort((left, right) => left - right)

function nearestCoefficient(wanted: number): number {
  let best = 1
  let bestDistance = Number.POSITIVE_INFINITY
  for (const value of COEFFICIENTS) {
    const distance = Math.abs(Math.log10(value / wanted))
    if (distance < bestDistance) {
      best = value
      bestDistance = distance
    }
  }
  return best
}

function isSameValue(left: number, right: number): boolean {
  return Math.abs(Math.log10(left / right)) < SAME_VALUE_TOLERANCE
}

function objectIdOf(system: ScaleSystem, name: string): string {
  const anchor = system.anchors.find((item) => item.name === name)
  if (anchor) return anchor.object ?? `anchor:${name}`
  if (system.rulers.some((ruler) => ruler.name === name)) return `ruler:${name}`
  return `manual:${name}`
}

function toPoolEntry(system: ScaleSystem, item: NamedValue, everyday = false): PoolEntry {
  return { ...item, object: objectIdOf(system, item.name), everyday }
}

/**
 * a 是任意给定的量；b 是「拿它来比」的有名字的东西；
 * c、d 是要给画面的，所以要求看得见、在地球范围内。
 */
export function buildPools(system: ScaleSystem, target: number) {
  const rulers: PoolEntry[] = system.rulers.map((ruler) => toPoolEntry(system, ruler, true))
  const named: PoolEntry[] = system.anchors
    .filter((anchor) => !anchor.abstract)
    .map((anchor) => toPoolEntry(system, anchor, Boolean(anchor.everyday)))
  const visible: PoolEntry[] = [
    ...named.filter((entry) => entry.everyday && entry.value >= system.visible.min && entry.value <= system.visible.max),
    ...rulers.filter((ruler) => ruler.value >= system.visible.min && ruler.value <= system.visible.max),
  ]

  return {
    bridges: [...named, ...rulers].filter((entry) => !isSameValue(entry.value, target)),
    right: visible.filter((entry) => !isSameValue(entry.value, target)),
  }
}

export function findProportions(system: ScaleSystem, target: number, options: FindProportionsOptions = {}): ProportionSuggestion[] {
  if (!(target > 0)) return []
  const limit = options.limit ?? 4
  const maxError = options.maxError ?? 0.2
  const minSpan = options.minSpan ?? 1
  const maxSpan = options.maxSpan ?? 7
  const targetName = options.targetName ?? formatWithUnits(system, target)
  const fix = options.fix ?? {}

  const { bridges, right } = buildPools(system, target)
  const bPool = fix.b ? [toPoolEntry(system, fix.b)] : bridges
  const cPool = fix.c ? [toPoolEntry(system, fix.c)] : right
  const dPool = fix.d ? [toPoolEntry(system, fix.d)] : right

  const found: { suggestion: ProportionSuggestion; rank: number[] }[] = []
  for (const b of bPool) {
    const wanted = target / b.value
    const span = Math.abs(Math.log10(wanted))
    if (!fix.b && (span < minSpan || span > maxSpan)) continue

    for (const c of cPool) {
      for (const d of dPool) {
        if (c.object === d.object || isSameValue(c.value, d.value)) continue
        if (b.object === c.object || b.object === d.object) continue
        if (isSameValue(b.value, c.value) || isSameValue(b.value, d.value)) continue

        const raw = wanted / (c.value / d.value)
        const coefficient = nearestCoefficient(raw >= 1 ? raw : 1 / raw)
        const coefficientSide: 'c' | 'd' = raw >= 1 ? 'c' : 'd'
        const adjusted = coefficientSide === 'c' ? c.value * coefficient / d.value : c.value / (d.value * coefficient)
        const error = adjusted / wanted - 1
        if (Math.abs(error) > maxError) continue

        const prefix = coefficient === 1 ? '' : `${formatSignificant(coefficient, 3)} × `
        const left = coefficientSide === 'c' ? `${prefix}${c.name}` : c.name
        const rightText = coefficientSide === 'd' ? `${prefix}${d.name}` : d.name
        const rulerTerms = [c, d].filter((term) => term.object.startsWith('ruler:')).length
        const bridgeFit = !b.everyday
          ? 2
          : b.value >= system.bridge.min && b.value <= system.bridge.max ? 0 : 1

        found.push({
          suggestion: {
            terms: {
              a: { value: target, name: targetName },
              b: { value: b.value, name: b.name },
              c: { value: c.value, name: c.name },
              d: { value: d.value, name: d.name },
            },
            coefficient,
            coefficientSide,
            factor: 1 / raw,
            span,
            error,
            text: `${targetName} : ${b.name} = ${left} : ${rightText}`,
          },
          // b 合不合适 → 左边能不能写成乘法 → 残差 → 要不要带系数 → 系数多大 → 两端是不是实物 → 跨度
          rank: [
            bridgeFit,
            !(fix.c && fix.d) && coefficient !== 1 && coefficientSide === 'c' ? 1 : 0,
            Math.round(Math.abs(error) * 1e4),
            coefficient === 1 ? 0 : 1,
            coefficient,
            rulerTerms,
            Math.abs(span - SPAN_SWEET_SPOT) > 1.5 ? 1 : 0,
            Math.abs(span - SPAN_SWEET_SPOT),
          ],
        })
      }
    }
  }

  found.sort((left, right) => {
    for (let index = 0; index < left.rank.length; index += 1) {
      if (left.rank[index] !== right.rank[index]) return left.rank[index] - right.rank[index]
    }
    return 0
  })

  const picked: ProportionSuggestion[] = []
  const seenTermSets = new Set<string>()
  const seenBridges = new Set<string>()
  const seenPairs = new Set<string>()
  for (const { suggestion } of found) {
    const terms = [suggestion.terms.b.name, suggestion.terms.c.name, suggestion.terms.d.name]
    const pairKey = [suggestion.terms.c.name, suggestion.terms.d.name].sort().join('|')
    const setKey = terms.slice().sort().join('|')
    if (seenTermSets.has(setKey)) continue
    if (!fix.c && !fix.d && seenPairs.has(pairKey)) continue
    if (!fix.b && seenBridges.has(suggestion.terms.b.name)) continue
    seenTermSets.add(setKey)
    seenBridges.add(suggestion.terms.b.name)
    seenPairs.add(pairKey)
    picked.push(suggestion)
    if (picked.length >= limit) break
  }
  return picked
}

export interface ChainGroup {
  c: NamedValue
  d: NamedValue
  ratio: number
}

export interface ScaleChain {
  b: NamedValue
  groups: ChainGroup[]
  factor: number
}

export interface FindChainOptions {
  b: NamedValue
  maxGroups?: number
}

/**
 * b 钉死之后，一组分式可能装不下 target ÷ b（一组最多跨池子能表达的量级）。
 * 这时把右边拆成几组相乘：a/b = (c₁/d₁)·(c₂/d₂)·…，左边的微调值依旧保证精确。
 */
export function findChain(system: ScaleSystem, target: number, options: FindChainOptions): ScaleChain | null {
  if (!(target > 0) || !(options.b.value > 0)) return null
  const maxGroups = options.maxGroups ?? 4
  const { right } = buildPools(system, target)
  const b = options.b

  const pairs: ChainGroup[] = []
  for (const c of right) {
    for (const d of right) {
      if (c.object === d.object || isSameValue(c.value, d.value)) continue
      pairs.push({ c, d, ratio: c.value / d.value })
    }
  }
  if (pairs.length === 0) return null

  const sorted = [...pairs].sort((left, right) => Math.log10(left.ratio) - Math.log10(right.ratio))
  const logs = sorted.map((pair) => Math.log10(pair.ratio))

  function nearestPair(wantLog: number, avoidObjects: Set<string>, avoidPairs: Set<string>): ChainGroup | null {
    let low = 0
    let high = logs.length - 1
    while (low < high) {
      const mid = (low + high) >> 1
      if (logs[mid] < wantLog) low = mid + 1
      else high = mid
    }
    const order: number[] = []
    for (let delta = 0; delta < logs.length; delta += 1) {
      if (delta === 0) order.push(low)
      else {
        if (low + delta < sorted.length) order.push(low + delta)
        if (low - delta >= 0) order.push(low - delta)
      }
    }
    for (const index of order) {
      const pair = sorted[index]
      // 相邻两组不许共用参照物，否则会互相抵消；同一对也不重复用
      if (avoidObjects.has(pair.c.name) || avoidObjects.has(pair.d.name)) continue
      if (avoidPairs.has([pair.c.name, pair.d.name].sort().join('|'))) continue
      return pair
    }
    return null
  }

  const totalLog = Math.log10(target / b.value)
  const maxDecades = Math.max(...logs.map(Math.abs)) || 1
  const minCount = Math.max(1, Math.ceil(Math.abs(totalLog) / maxDecades))

  /** 可见参照物，按大小排好，用来找「最接近某个值」的那一个 */
  const visibleSorted = [...new Set(right.map((entry) => entry.name))]
    .map((name) => right.find((entry) => entry.name === name))
    .filter((entry): entry is PoolEntry => entry !== undefined)
    .sort((left, right) => left.value - right.value)
  const visibleLogs = visibleSorted.map((entry) => Math.log10(entry.value))

  function nearestVisible(needed: number): NamedValue | null {
    if (visibleSorted.length === 0 || !(needed > 0)) return null
    const wantLog = Math.log10(needed)
    let low = 0
    let high = visibleLogs.length - 1
    while (low < high) {
      const mid = (low + high) >> 1
      if (visibleLogs[mid] < wantLog) low = mid + 1
      else high = mid
    }
    const candidates = [low, low - 1].filter((index) => index >= 0 && index < visibleSorted.length)
    candidates.sort((a, c) => Math.abs(visibleLogs[a] - wantLog) - Math.abs(visibleLogs[c] - wantLog))
    for (const index of candidates) {
      if (isSameValue(visibleSorted[index].value, needed)) continue
      return visibleSorted[index]
    }
    return null
  }

  /**
   * 拆成几组时优先「共用同一个分母」：把最大的那个可见参照物当共同基准，
   * 每组只换分子。这样念出来就是「这些小东西各自相对于同一个大东西」。
   */
  function attemptShared(count: number): ScaleChain | null {
    const denominator = visibleSorted[visibleSorted.length - 1]
    if (!denominator || isSameValue(denominator.value, b.value)) return null
    const groups: ChainGroup[] = []
    let accumulated = 0
    for (let index = 0; index < count; index += 1) {
      const want = (totalLog - accumulated) / (count - index)
      const numerator = nearestVisible(denominator.value * 10 ** want)
      if (!numerator) return null
      groups.push({ c: numerator, d: denominator, ratio: numerator.value / denominator.value })
      accumulated += Math.log10(numerator.value / denominator.value)
    }
    return { b, groups, factor: 10 ** (accumulated - totalLog) }
  }

  function attemptWith(count: number): ScaleChain | null {
    const groups: ChainGroup[] = []
    const avoidPairs = new Set<string>()
    let avoidObjects = new Set<string>()
    let accumulated = 0
    for (let index = 0; index < count; index += 1) {
      const want = (totalLog - accumulated) / (count - index)
      const pair = nearestPair(want, avoidObjects, avoidPairs)
      if (!pair) break
      avoidPairs.add([pair.c.name, pair.d.name].sort().join('|'))
      avoidObjects = new Set([pair.c.name, pair.d.name])
      groups.push(pair)
      accumulated += Math.log10(pair.ratio)
    }
    if (groups.length === 0) return null
    return { b, groups, factor: 10 ** (accumulated - totalLog) }
  }

  // 从最少组数开始试。两组以上优先共用一个分母；哪一档能让左边那个微调值贴住 1，就用哪一档
  let fallback: ScaleChain | null = null
  const consider = (attempt: ScaleChain | null) => {
    if (!attempt) return false
    if (Math.abs(Math.log10(attempt.factor)) <= 0.2) {
      fallback = attempt
      return true
    }
    if (!fallback || Math.abs(Math.log10(attempt.factor)) < Math.abs(Math.log10(fallback.factor))) fallback = attempt
    return false
  }

  for (let count = minCount; count <= maxGroups; count += 1) {
    if (count > 1 && consider(attemptShared(count))) return fallback
    if (consider(attemptWith(count))) return fallback
  }
  return fallback
}
