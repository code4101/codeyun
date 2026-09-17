import type { NamedValue, ScaleAnchor, ScaleCategory, ScaleSystem, ScaleUnit } from '@/utils/scale'

export const AREA_UNITS: ScaleUnit[] = [
  { id: 'mm2', name: '平方毫米', symbol: 'mm²', factor: 1e-6 },
  { id: 'cm2', name: '平方厘米', symbol: 'cm²', factor: 1e-4 },
  { id: 'm2', name: '平方米', symbol: 'm²', factor: 1 },
  { id: 'are', name: '公亩', symbol: 'a', factor: 100 },
  { id: 'mu', name: '亩', symbol: '亩', factor: 666.6666667 },
  { id: 'ha', name: '公顷', symbol: 'ha', factor: 1e4 },
  { id: 'km2', name: '平方千米', symbol: 'km²', factor: 1e6 },
  { id: 'ft2', name: '平方英尺', symbol: 'ft²', factor: 0.09290304 },
  { id: 'acre', name: '英亩', symbol: 'ac', factor: 4046.8564224 },
  { id: 'mi2', name: '平方英里', symbol: 'mi²', factor: 2589988.110336 },
]

export const AREA_CATEGORIES: ScaleCategory[] = [
  { id: 'handy', title: '手里', color: '#22c55e' },
  { id: 'ground', title: '场地', color: '#0ea5e9' },
  { id: 'geo', title: '地理', color: '#f59e0b' },
  { id: 'planet', title: '行星', color: '#a0224a' },
]

export const AREA_ANCHORS: ScaleAnchor[] = [
  { value: 4.9e-4, name: '一枚一元硬币', category: 'handy', note: '约 4.9 平方厘米', object: 'coin', familiarity: 1, everyday: true },
  { value: 6.24e-2, name: '一张 A4 纸', category: 'handy', note: '21 × 29.7 厘米', object: 'a4', familiarity: 1, everyday: true },
  { value: 8e-2, name: '一个鼠标垫', category: 'handy', note: '约 800 平方厘米', object: 'mousemat', familiarity: 1, everyday: true },
  { value: 3, name: '一张双人床', category: 'handy', note: '2 × 1.5 米', object: 'bed', familiarity: 1, everyday: true },
  { value: 12, name: '一个停车位', category: 'ground', note: '约 12 平方米', object: 'parking', familiarity: 1, everyday: true },
  { value: 81.74, name: '一个羽毛球场', category: 'ground', note: '13.4 × 6.1 米', object: 'badminton', familiarity: 1, everyday: true },
  { value: 420, name: '一个篮球场', category: 'ground', note: '28 × 15 米', object: 'basketball', familiarity: 1, everyday: true },
  { value: 7140, name: '一个足球场', category: 'ground', note: '105 × 68 米', object: 'pitch', familiarity: 1, everyday: true },
  { value: 666.6666667, name: '一亩地', category: 'ground', note: '约 666.7 平方米', object: 'mu', familiarity: 1, everyday: true },
  { value: 4.4e5, name: '天安门广场', category: 'geo', note: '约 44 万平方米', object: 'tiananmen', familiarity: 1, everyday: true },
  { value: 7.2e5, name: '故宫', category: 'geo', note: '约 72 万平方米', object: 'palace', familiarity: 1, everyday: true },
  { value: 6.38e6, name: '西湖', category: 'geo', note: '约 6.38 平方千米', object: 'westlake', familiarity: 1, everyday: true },
  { value: 3.3e7, name: '澳门', category: 'geo', note: '约 33 平方千米', object: 'macau', familiarity: 1, everyday: true },
  { value: 1.1e9, name: '香港', category: 'geo', note: '约 1100 平方千米', object: 'hongkong', familiarity: 1, everyday: true },
  { value: 6.34e9, name: '上海', category: 'geo', note: '约 6340 平方千米', object: 'shanghai', familiarity: 1, everyday: true },
  { value: 9.6e12, name: '中国', category: 'geo', note: '约 960 万平方千米', object: 'china', familiarity: 1, everyday: true },
  { value: 1.65e14, name: '太平洋', category: 'planet', note: '约 1.65 亿平方千米', object: 'pacific', familiarity: 1, everyday: true },
  { value: 5.1e14, name: '地球表面积', category: 'planet', note: '约 5.1 亿平方千米', object: 'earth', familiarity: 2, everyday: true },
]

export const AREA_RULERS: NamedValue[] = [
  { value: 1e-4, name: '1 平方厘米' },
  { value: 1, name: '1 平方米' },
  { value: 1e4, name: '1 公顷' },
  { value: 1e6, name: '1 平方千米' },
]

export const areaSystem: ScaleSystem = {
  id: 'area',
  title: '面积',
  baseUnit: '平方米',
  units: AREA_UNITS,
  anchors: AREA_ANCHORS,
  rulers: AREA_RULERS,
  // 一平方厘米到地球表面积
  visible: { min: 1e-4, max: 5.1e14 },
  // b 默认从一平方厘米到一平方千米
  bridge: { min: 1e-4, max: 1e6 },
  displayUnits: ['cm2', 'm2', 'ha', 'km2'],
  defaultUnit: 'm2',
  categories: AREA_CATEGORIES,
}
