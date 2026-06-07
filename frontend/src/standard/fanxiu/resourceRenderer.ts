import type { FanxiuGongfaLinkedItem, FanxiuWikiLinkIndexItem } from '@/api/fanxiu'

export type FanxiuResourceType = 'gongfa' | 'item' | 'lingjie' | 'activity' | 'digitdoor' | 'doupotd'

export type FanxiuResourceLinkTarget = Pick<
  FanxiuWikiLinkIndexItem,
  'alias' | 'tab' | 'id' | 'title' | 'preview' | 'effect_text_preview' | 'effect_preview' | 'reward_preview' | 'kind' | 'priority'
>

export type FanxiuEffectRow = {
  key: string
  name: string
  value: string
}

export type FanxiuRewardRow = {
  id?: string | number
  name?: string
  count?: string | number
  icon?: string
  description?: string
}

const FORMULA_EDGE_RE = /[0-9=+\-*/×()（）]/

export function normalizeFanxiuRichText(value: unknown) {
  return String(value || '')
    .replace(/<color=#[0-9a-fA-F]{3,8}>/g, '')
    .replace(/<\/color>/g, '')
    .replace(/<size=[0-9]{1,3}>/g, '')
    .replace(/<\/size>/g, '')
}

export function cleanFanxiuPreview(value: unknown) {
  return normalizeFanxiuRichText(value)
    .replace(/\s+/g, ' ')
    .trim()
}

export function cleanFanxiuDisplayText(value: unknown) {
  return normalizeFanxiuRichText(value)
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .replace(/\u3000/g, ' ')
    .trim()
}

export function sameFanxiuPreview(left: unknown, right: unknown) {
  const leftText = cleanFanxiuPreview(left)
  return Boolean(leftText) && leftText === cleanFanxiuPreview(right)
}

export function escapeFanxiuHtml(value: unknown) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

export function buildFanxiuResourceHref(tab: FanxiuResourceType, id: string | number) {
  return `/fanxiu-resource/${encodeURIComponent(tab)}/${encodeURIComponent(String(id))}`
}

export function encodeFanxiuDataText(value: unknown) {
  return encodeURIComponent(cleanFanxiuDisplayText(value))
}

export function buildFanxiuLinkTargetGroups(targets: FanxiuResourceLinkTarget[]) {
  const groups = new Map<string, FanxiuResourceLinkTarget[]>()
  for (const target of targets) {
    const alias = cleanFanxiuPreview(target.alias)
    if (!alias) continue
    const first = alias[0]
    const group = groups.get(first) ?? []
    group.push({ ...target, alias })
    groups.set(first, group)
  }
  for (const group of groups.values()) {
    group.sort((left, right) => {
      const length = cleanFanxiuPreview(right.alias).length - cleanFanxiuPreview(left.alias).length
      if (length) return length
      const priority = Number(right.priority ?? 0) - Number(left.priority ?? 0)
      if (priority) return priority
      return cleanFanxiuPreview(left.alias).localeCompare(cleanFanxiuPreview(right.alias), 'zh-Hans-CN')
    })
  }
  return groups
}

export function renderFanxiuResourceLinkTarget(target: FanxiuResourceLinkTarget) {
  const aliasText = cleanFanxiuPreview(target.alias)
  const alias = escapeFanxiuHtml(aliasText)
  const tab = target.tab as FanxiuResourceType
  const id = String(target.id ?? '').trim()
  if (
    !aliasText
    || !id
    || !['gongfa', 'item', 'lingjie', 'activity', 'digitdoor', 'doupotd'].includes(tab)
  ) return alias
  const href = buildFanxiuResourceHref(tab, id)
  const title = escapeFanxiuHtml(target.title || aliasText)
  const preview = escapeFanxiuHtml(encodeFanxiuDataText(target.preview))
  const effectTextPreview = escapeFanxiuHtml(encodeFanxiuDataText(target.effect_text_preview))
  const effectPreview = escapeFanxiuHtml(cleanFanxiuPreview(target.effect_preview))
  const rewardPreview = escapeFanxiuHtml(String(target.reward_preview || ''))
  return `<a class="fanxiu-resource-link" href="${href}" data-fanxiu-resource-link="1" data-wiki-resource-link="1" data-wiki-tab="${tab}" data-wiki-id="${escapeFanxiuHtml(id)}" data-wiki-title="${title}" data-wiki-preview="${preview}" data-wiki-effect-text-preview="${effectTextPreview}" data-wiki-effect-preview="${effectPreview}" data-wiki-reward-preview="${rewardPreview}" data-wiki-alias="${alias}">${alias}</a>`
}

export function renderFanxiuPlainRichText(value: unknown) {
  return escapeFanxiuHtml(normalizeFanxiuRichText(value))
    .replace(/【([^】]{1,30})】/g, '<span class="fanxiu-rich-term">【$1】</span>')
    .replace(/[xXyYzZ]/g, (match, offset, source) => {
      const before = source[offset - 1] ?? ''
      const after = source[offset + 1] ?? ''
      const isFormulaEdge = FORMULA_EDGE_RE.test(before) || FORMULA_EDGE_RE.test(after)
      return isFormulaEdge ? `<span class="fanxiu-rich-variable">${match}</span>` : match
    })
    .replace(/([+＋]\s*\d+(?:\.\d+)?%?|\d+(?:\.\d+)?%)/g, '<span class="fanxiu-rich-number">$1</span>')
}

export function renderFanxiuRichText(
  value: unknown,
  linkTargetGroups?: Map<string, FanxiuResourceLinkTarget[]>,
) {
  const text = normalizeFanxiuRichText(value)
  if (!text || !linkTargetGroups?.size) return renderFanxiuPlainRichText(text)
  let output = ''
  let index = 0
  let plainStart = 0
  while (index < text.length) {
    const group = linkTargetGroups.get(text[index])
    const target = group?.find(item => {
      const alias = cleanFanxiuPreview(item.alias)
      return alias && text.startsWith(alias, index)
    })
    if (!target) {
      index += 1
      continue
    }
    output += renderFanxiuPlainRichText(text.slice(plainStart, index))
    output += renderFanxiuResourceLinkTarget(target)
    index += cleanFanxiuPreview(target.alias).length
    plainStart = index
  }
  return output + renderFanxiuPlainRichText(text.slice(plainStart))
}

export function parseFanxiuEffectRows(value: unknown): FanxiuEffectRow[] {
  return String(value ?? '')
    .split('|')
    .map(part => part.trim())
    .filter(Boolean)
    .map(part => {
      const [name, rawValue, unit] = part.split('_')
      const valueText = [rawValue, unit].filter(Boolean).join('')
      return {
        key: part,
        name: name || part,
        value: valueText ? `+${valueText}` : '',
      }
    })
}

export function parseFanxiuRewardRows(value: unknown): FanxiuRewardRow[] {
  if (!value) return []
  try {
    const parsed = JSON.parse(String(value))
    return Array.isArray(parsed)
      ? parsed
        .map(item => item as FanxiuRewardRow)
        .filter(item => item && (item.name || item.id))
      : []
  } catch {
    return []
  }
}

export function buildFanxiuRewardPreview(rewards: FanxiuGongfaLinkedItem[] | undefined) {
  if (!rewards?.length) return ''
  return JSON.stringify(rewards.slice(0, 20).map(item => ({
    id: item.id,
    name: item.name,
    count: item.count,
    icon: item.icon,
    description: cleanFanxiuPreview(item.description),
  })))
}
