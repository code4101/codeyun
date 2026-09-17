import type { NamedValue, ScaleAnchor, ScaleCategory, ScaleSystem, ScaleUnit } from '@/utils/scale'

export const SPEED_UNITS: ScaleUnit[] = [
  { id: 'mms', name: '毫米每秒', symbol: 'mm/s', factor: 1e-3 },
  { id: 'm/s', name: '米每秒', symbol: 'm/s', factor: 1 },
  { id: 'km/h', name: '千米每小时', symbol: 'km/h', factor: 1 / 3.6 },
  { id: 'mph', name: '英里每小时', symbol: 'mph', factor: 0.44704 },
  { id: 'knot', name: '节', symbol: 'kn', factor: 1852 / 3600 },
  { id: 'mach', name: '马赫', symbol: 'Ma', factor: 340.3 },
  { id: 'c', name: '光速', symbol: 'c', factor: 2.99792458e8 },
]

export const SPEED_CATEGORIES: ScaleCategory[] = [
  { id: 'slow', title: '慢', color: '#22c55e' },
  { id: 'vehicle', title: '载具', color: '#0ea5e9' },
  { id: 'fast', title: '极快', color: '#f59e0b' },
  { id: 'cosmic', title: '宇宙', color: '#a0224a' },
]

export const SPEED_ANCHORS: ScaleAnchor[] = [
  { value: 1e-3, name: '蜗牛爬行', category: 'slow', note: '约 0.001 米每秒', object: 'snail', familiarity: 1, everyday: true },
  { value: 1.4, name: '人走路', category: 'slow', note: '约 5 千米每小时', object: 'walk', familiarity: 1, everyday: true },
  { value: 3, name: '人跑步', category: 'slow', note: '约 11 千米每小时', object: 'run', familiarity: 1, everyday: true },
  { value: 4.5, name: '骑自行车', category: 'slow', note: '约 16 千米每小时', object: 'bike', familiarity: 1, everyday: true },
  { value: 14, name: '市区开车', category: 'vehicle', note: '约 50 千米每小时', object: 'city-car', familiarity: 1, everyday: true },
  { value: 28, name: '高速开车', category: 'vehicle', note: '约 100 千米每小时', object: 'highway-car', familiarity: 1, everyday: true },
  { value: 83, name: '高铁', category: 'vehicle', note: '约 300 千米每小时', object: 'hsr', familiarity: 1, everyday: true },
  { value: 250, name: '民航客机', category: 'vehicle', note: '约 900 千米每小时', object: 'airliner', familiarity: 1, everyday: true },
  { value: 340.3, name: '声速', category: 'fast', note: '空气中海平面约 340 米每秒', object: 'sound', familiarity: 1, everyday: true },
  { value: 800, name: '步枪子弹', category: 'fast', note: '约 800 米每秒', object: 'bullet', familiarity: 1, everyday: true },
  { value: 7900, name: '第一宇宙速度', category: 'fast', note: '环绕地球所需 7.9 千米每秒', object: 'v1', familiarity: 2, everyday: true },
  { value: 11200, name: '第二宇宙速度', category: 'fast', note: '脱离地球引力 11.2 千米每秒', object: 'v2', familiarity: 2 },
  { value: 2.99792458e8, name: '光速', category: 'cosmic', note: '约 30 万千米每秒', object: 'light', familiarity: 1, everyday: true },
]

export const SPEED_RULERS: NamedValue[] = [
  { value: 1, name: '1 米/秒' },
  { value: 340.3, name: '1 马赫' },
]

export const speedSystem: ScaleSystem = {
  id: 'speed',
  title: '速度',
  baseUnit: '米每秒',
  units: SPEED_UNITS,
  anchors: SPEED_ANCHORS,
  rulers: SPEED_RULERS,
  // 走路到光速
  visible: { min: 1, max: 2.99792458e8 },
  // b 默认从走路到客机
  bridge: { min: 1, max: 250 },
  displayUnits: ['m/s', 'km/h', 'mach', 'c'],
  defaultUnit: 'm/s',
  categories: SPEED_CATEGORIES,
}
