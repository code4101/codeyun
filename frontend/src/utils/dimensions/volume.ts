import type { NamedValue, ScaleAnchor, ScaleCategory, ScaleSystem, ScaleUnit } from '@/utils/scale'

export const VOLUME_UNITS: ScaleUnit[] = [
  { id: 'ml', name: '毫升', symbol: 'mL', factor: 1e-6, aliases: ['cm3', '立方厘米'] },
  { id: 'cl', name: '厘升', symbol: 'cL', factor: 1e-5 },
  { id: 'dl', name: '分升', symbol: 'dL', factor: 1e-4 },
  { id: 'l', name: '升', symbol: 'L', factor: 1e-3, aliases: ['立方分米'] },
  { id: 'm3', name: '立方米', symbol: 'm³', factor: 1 },
  { id: 'ft3', name: '立方英尺', symbol: 'ft³', factor: 0.028316846592 },
  { id: 'gal', name: '美制加仑', symbol: 'gal', factor: 3.785411784e-3 },
  { id: 'bbl', name: '石油桶', symbol: 'bbl', factor: 0.158987294928 },
  { id: 'km3', name: '立方千米', symbol: 'km³', factor: 1e9 },
]

export const VOLUME_CATEGORIES: ScaleCategory[] = [
  { id: 'handy', title: '手里', color: '#22c55e' },
  { id: 'container', title: '容器', color: '#0ea5e9' },
  { id: 'water', title: '水体', color: '#f59e0b' },
  { id: 'planet', title: '行星', color: '#a0224a' },
]

export const VOLUME_ANCHORS: ScaleAnchor[] = [
  { value: 5e-8, name: '一滴水', category: 'handy', note: '约 0.05 毫升', object: 'drop', familiarity: 1, everyday: true },
  { value: 5e-6, name: '一勺水', category: 'handy', note: '约 5 毫升', object: 'spoon', familiarity: 1, everyday: true },
  { value: 2e-4, name: '一口水', category: 'handy', note: '约 200 毫升', object: 'sip', familiarity: 1, everyday: true },
  { value: 1.25e-3, name: '一罐可乐', category: 'handy', note: '330 毫升', object: 'cola', familiarity: 1, everyday: true },
  { value: 5.5e-4, name: '一瓶矿泉水', category: 'handy', note: '550 毫升', object: 'bottle', familiarity: 1, everyday: true },
  { value: 1.89e-2, name: '一桶饮水机水', category: 'container', note: '18.9 升', object: 'watercooler', familiarity: 1, everyday: true },
  { value: 2e-1, name: '一个浴缸', category: 'container', note: '约 200 升', object: 'bathtub', familiarity: 1, everyday: true },
  { value: 33, name: '一个集装箱', category: 'container', note: '标准 20 尺柜约 33 立方米', object: 'container', familiarity: 1, everyday: true },
  { value: 2.5e3, name: '一个标准泳池', category: 'container', note: '50 × 25 × 2 米', object: 'pool', familiarity: 1, everyday: true },
  { value: 1.4e7, name: '西湖', category: 'water', note: '约 1400 万立方米', object: 'westlake', familiarity: 1, everyday: true },
  { value: 4.4e9, name: '太湖', category: 'water', note: '约 44 亿立方米', object: 'taihu', familiarity: 1, everyday: true },
  { value: 1.7e12, name: '渤海', category: 'water', note: '约 1.7 万亿立方米', object: 'bohai', familiarity: 1, everyday: true },
  { value: 1.386e18, name: '地球上的水', category: 'planet', note: '约 13.86 亿立方千米', object: 'earth-water', familiarity: 2, everyday: true },
  { value: 1.083e21, name: '地球体积', category: 'planet', note: '约 1.083 万亿立方千米', object: 'earth', familiarity: 2 },
]

export const VOLUME_RULERS: NamedValue[] = [
  { value: 1e-6, name: '1 毫升' },
  { value: 1e-3, name: '1 升' },
  { value: 1, name: '1 立方米' },
]

export const volumeSystem: ScaleSystem = {
  id: 'volume',
  title: '体积',
  baseUnit: '立方米',
  units: VOLUME_UNITS,
  anchors: VOLUME_ANCHORS,
  rulers: VOLUME_RULERS,
  // 一毫升到地球体积
  visible: { min: 1e-6, max: 1.083e21 },
  // b 默认从一毫升到一立方米
  bridge: { min: 1e-6, max: 1 },
  displayUnits: ['ml', 'l', 'm3', 'km3'],
  defaultUnit: 'l',
  categories: VOLUME_CATEGORIES,
}
