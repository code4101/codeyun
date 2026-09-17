import type { NamedValue, ScaleAnchor, ScaleCategory, ScaleSystem, ScaleUnit } from '@/utils/scale'

export const TIME_UNITS: ScaleUnit[] = [
  { id: 'ns', name: '纳秒', symbol: 'ns', factor: 1e-9 },
  { id: 'us', name: '微秒', symbol: 'µs', factor: 1e-6, aliases: ['μs', 'us'] },
  { id: 'ms', name: '毫秒', symbol: 'ms', factor: 1e-3 },
  { id: 's', name: '秒', symbol: 's', factor: 1 },
  { id: 'min', name: '分钟', symbol: 'min', factor: 60 },
  { id: 'h', name: '小时', symbol: 'h', factor: 3600 },
  { id: 'day', name: '天', symbol: 'd', factor: 86400, aliases: ['日'] },
  { id: 'week', name: '周', symbol: 'wk', factor: 604800 },
  { id: 'month', name: '月', symbol: 'mo', factor: 2.628e6, aliases: ['个月'] },
  { id: 'year', name: '年', symbol: 'yr', factor: 3.155693e7, aliases: ['a'] },
  { id: 'century', name: '世纪', symbol: 'century', factor: 3.155693e9 },
  { id: 'kyr', name: '千年', symbol: 'kyr', factor: 3.155693e10 },
  { id: 'myr', name: '百万年', symbol: 'Myr', factor: 3.155693e13 },
  { id: 'gyr', name: '十亿年', symbol: 'Gyr', factor: 3.155693e16 },
]

export const TIME_CATEGORIES: ScaleCategory[] = [
  { id: 'instant', title: '一瞬间', color: '#8b5cf6' },
  { id: 'daily', title: '日常', color: '#22c55e' },
  { id: 'life', title: '人生', color: '#0ea5e9' },
  { id: 'history', title: '历史与地质', color: '#f59e0b' },
  { id: 'cosmic', title: '宇宙', color: '#a0224a' },
]

export const TIME_ANCHORS: ScaleAnchor[] = [
  { value: 1e-9, name: '一次闪电', category: 'instant', note: '约 1 微秒内完成放电', familiarity: 2, abstract: true },
  { value: 3e-1, name: '一次眨眼', category: 'instant', note: '约 0.3 秒', object: 'blink', familiarity: 1, everyday: true },
  { value: 8e-1, name: '一次心跳', category: 'instant', note: '约 0.8 秒', object: 'heartbeat', familiarity: 1, everyday: true },
  { value: 4, name: '一次深呼吸', category: 'instant', note: '约 4 秒', object: 'breath', familiarity: 1, everyday: true },
  { value: 6e1, name: '一分钟', category: 'daily', object: 'minute', familiarity: 1, everyday: true },
  { value: 9e2, name: '一刻钟', category: 'daily', note: '15 分钟', object: 'quarter', familiarity: 1, everyday: true },
  { value: 2.7e3, name: '一节课', category: 'daily', note: '45 分钟', object: 'class', familiarity: 1, everyday: true },
  { value: 7.2e3, name: '一场电影', category: 'daily', note: '2 小时', object: 'movie', familiarity: 1, everyday: true },
  { value: 1.44e4, name: '跑一场马拉松', category: 'daily', note: '约 4 小时', object: 'marathon', familiarity: 1, everyday: true },
  { value: 2.88e4, name: '睡一觉', category: 'daily', note: '8 小时', object: 'sleep', familiarity: 1, everyday: true },
  { value: 8.64e4, name: '一整天', category: 'daily', object: 'day', familiarity: 1, everyday: true },
  { value: 6.048e5, name: '一周', category: 'daily', object: 'week', familiarity: 1, everyday: true },
  { value: 2.628e6, name: '一个月', category: 'daily', object: 'month', familiarity: 1, everyday: true },
  { value: 3.155693e7, name: '一年', category: 'life', object: 'year', familiarity: 1, everyday: true },
  { value: 3.155693e8, name: '十年', category: 'life', object: 'decade', familiarity: 1, everyday: true },
  { value: 2.5e9, name: '人的一生', category: 'life', note: '约 80 年', object: 'lifetime', familiarity: 1, everyday: true },
  { value: 9.5e9, name: '一个朝代', category: 'history', note: '约 300 年', object: 'dynasty', familiarity: 2 },
  { value: 3.155693e11, name: '人类文明史', category: 'history', note: '约 1 万年', object: 'civilization', familiarity: 1, everyday: true },
  { value: 3.155693e13, name: '人类史', category: 'history', note: '约 100 万年', object: 'human-history', familiarity: 2 },
  { value: 2.08e15, name: '恐龙灭绝至今', category: 'history', note: '约 6600 万年', object: 'dinosaur', familiarity: 1, everyday: true },
  { value: 1.43e17, name: '地球年龄', category: 'cosmic', note: '约 45.4 亿年', object: 'earth-age', familiarity: 1, everyday: true },
  { value: 4.35e17, name: '宇宙年龄', category: 'cosmic', note: '约 138 亿年', object: 'universe-age', familiarity: 2 },
]

export const TIME_RULERS: NamedValue[] = [
  { value: 1, name: '1 秒' },
  { value: 60, name: '1 分钟' },
  { value: 3600, name: '1 小时' },
  { value: 86400, name: '1 天' },
  { value: 3.155693e7, name: '1 年' },
]

export const timeSystem: ScaleSystem = {
  id: 'time',
  title: '时间',
  baseUnit: '秒',
  units: TIME_UNITS,
  anchors: TIME_ANCHORS,
  rulers: TIME_RULERS,
  // 一秒到一年：能等、能感觉到的跨度
  visible: { min: 1, max: 3.155693e7 },
  // b 默认从一秒到一天
  bridge: { min: 1, max: 86400 },
  displayUnits: ['ns', 'us', 'ms', 's', 'min', 'h', 'day', 'year', 'myr', 'gyr'],
  defaultUnit: 's',
  categories: TIME_CATEGORIES,
}
