import { legacyMarkNote } from './legacyMarks'

/** 体表坐标是定位依据；部位名称只是可编辑的粗略提示。标注不预设业务用途。 */
export const views = [
  { id: 'front', label: '正面' }, { id: 'back', label: '背面' },
  { id: 'left', label: '本人左侧' }, { id: 'right', label: '本人右侧' },
  { id: 'top', label: '头顶' },
] as const
export type BodyView = typeof views[number]['id']
export interface BodyMark {
  id: string
  view: BodyView
  x: number
  y: number
  radius: number
  region: string
  note: string
}
export const storageKey = 'codeyun.body-map.v2'
export function regionAt(view: BodyView, x: number, y: number): string {
  if (view === 'top') return `${y < 72 ? '头顶前部' : y > 108 ? '头顶后部' : '头顶部'}${Math.abs(x - 200) < 12 ? '中央' : x < 200 ? '偏左' : '偏右'}`
  const side = view === 'left' ? '左' : view === 'right' ? '右'
    : Math.abs(x - 200) < 12 ? '' : (x < 200) === (view === 'front') ? '右' : '左'
  if (y < 135) {
    if (view === 'back') return `${side}${y < 63 ? '头顶后部' : '后脑部'}`
    if (view === 'left' || view === 'right') return `${side}侧${y < 53 ? '头顶部' : x > 210 ? '后脑部' : x < 172 ? '额面部' : '颞部／耳周'}`
    return `${side}${y < 53 ? '头顶前部' : y < 82 ? '额部' : y < 102 ? '眼周／太阳穴附近' : '面部'}`
  }
  if (y < 164) return `${side}颈部`
  if (x < 128 || x > 272) return `${side}${y > 342 ? '手部' : y > 270 ? '前臂' : '肩／上臂'}`
  if (y < 275) return `${side}${view === 'back' ? '上背部' : '胸部'}`
  if (y < 365) return `${side}${view === 'back' ? '腰背部' : '腹部'}`
  if (y < 430) return `${side}${view === 'back' ? '臀部' : '骨盆／腹股沟附近'}`
  return `${side}${y < 556 ? '大腿' : y < 605 ? '膝部' : y < 735 ? '小腿' : '足部'}`
}

/** 忽略损坏或不兼容的本地记录，避免一次异常存储使整个标注页不可用。 */
export function parseMarks(raw: string | null): BodyMark[] {
  if (!raw) return []
  try {
    const data: unknown = JSON.parse(raw)
    if (!Array.isArray(data)) return []
    const ids = new Set<string>()
    return data.filter((m) => {
      if (!m || typeof m !== 'object' || typeof m.id !== 'string' || ids.has(m.id)) return false
      if (!views.some(v => v.id === m.view) || !Number.isFinite(m.x) || !Number.isFinite(m.y)
        || !Number.isFinite(m.radius) || m.radius < 3 || m.radius > 35
        || !['region', 'note'].every(k => typeof m[k] === 'string')) return false
      ids.add(m.id)
      return true
    }).slice(0, 100).map(m => ({
      id: m.id, view: m.view, x: m.x, y: m.y, radius: m.radius,
      region: m.region, note: legacyMarkNote(m),
    }))
  } catch { return [] }
}

export function describeMarks(marks: BodyMark[]): string {
  return ['人体模型标注（男性体表示意，左右按本人；位置名称为粗略提示）',
    ...marks.map((m, i) => `${i + 1}. ${views.find(v => v.id === m.view)?.label} · ${m.region}：${m.note || '暂无说明'} [位置 ${Math.round(m.x)},${Math.round(m.y)}；范围 ${m.radius}]`),
  ].join('\n')
}
