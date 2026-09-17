import type { NamedValue, ScaleAnchor, ScaleCategory, ScaleSystem, ScaleUnit } from '@/utils/scale'

export const MASS_UNITS: ScaleUnit[] = [
  { id: 'ug', name: '微克', symbol: 'µg', factor: 1e-9, aliases: ['μg', 'ug'] },
  { id: 'mg', name: '毫克', symbol: 'mg', factor: 1e-6 },
  { id: 'ct', name: '克拉', symbol: 'ct', factor: 2e-4 },
  { id: 'g', name: '克', symbol: 'g', factor: 1e-3 },
  { id: 'qian', name: '钱', symbol: '钱', factor: 5e-3 },
  { id: 'liang', name: '两', symbol: '两', factor: 5e-2 },
  { id: 'jin', name: '斤', symbol: '斤', factor: 5e-1 },
  { id: 'kg', name: '千克', symbol: 'kg', factor: 1, aliases: ['公斤'] },
  { id: 'lb', name: '磅', symbol: 'lb', factor: 0.45359237, aliases: ['pound'] },
  { id: 'oz', name: '盎司', symbol: 'oz', factor: 0.028349523125 },
  { id: 't', name: '吨', symbol: 't', factor: 1e3 },
  { id: 'earth-mass', name: '地球质量', symbol: 'M⊕', factor: 5.9722e24 },
  { id: 'sun-mass', name: '太阳质量', symbol: 'M☉', factor: 1.98892e30 },
]

export const MASS_CATEGORIES: ScaleCategory[] = [
  { id: 'tiny', title: '很小', color: '#8b5cf6' },
  { id: 'handy', title: '手里', color: '#22c55e' },
  { id: 'body', title: '身体与重物', color: '#0ea5e9' },
  { id: 'huge', title: '庞然大物', color: '#f59e0b' },
  { id: 'cosmic', title: '天体', color: '#a0224a' },
]

export const MASS_ANCHORS: ScaleAnchor[] = [
  { value: 1e-6, name: '一粒盐', category: 'tiny', note: '约 1 毫克', object: 'salt', familiarity: 2 },
  { value: 2e-5, name: '一粒米', category: 'tiny', note: '约 0.02 克', object: 'rice', familiarity: 1, everyday: true },
  { value: 6.1e-3, name: '一枚一元硬币', category: 'handy', note: '6.1 克', object: 'coin', familiarity: 1, everyday: true },
  { value: 5e-3, name: '一张 A4 纸', category: 'handy', note: '约 5 克', object: 'a4', familiarity: 1, everyday: true },
  { value: 6e-2, name: '一个鸡蛋', category: 'handy', note: '约 60 克', object: 'egg', familiarity: 1, everyday: true },
  { value: 2e-1, name: '一个苹果', category: 'handy', note: '约 200 克', object: 'apple', familiarity: 1, everyday: true },
  { value: 5.5e-1, name: '一瓶矿泉水', category: 'handy', note: '550 毫升水约 550 克', object: 'bottle', familiarity: 1, everyday: true },
  { value: 4, name: '一只猫', category: 'body', note: '约 4 千克', object: 'cat', familiarity: 1, everyday: true },
  { value: 6.5e1, name: '一个成年人', category: 'body', note: '约 65 千克', object: 'human', familiarity: 1, everyday: true },
  { value: 1e2, name: '一头猪', category: 'body', note: '约 100 千克', object: 'pig', familiarity: 1, everyday: true },
  { value: 5e2, name: '一头牛', category: 'body', note: '约 500 千克', object: 'cow', familiarity: 1, everyday: true },
  { value: 1.5e3, name: '一辆汽车', category: 'body', note: '约 1.5 吨', object: 'car', familiarity: 1, everyday: true },
  { value: 5e3, name: '一头大象', category: 'huge', note: '约 5 吨', object: 'elephant', familiarity: 1, everyday: true },
  { value: 1.5e5, name: '一头蓝鲸', category: 'huge', note: '约 150 吨', object: 'whale', familiarity: 1, everyday: true },
  { value: 1.8e5, name: '一架客机', category: 'huge', note: '最大起飞重量约 180 吨', object: 'aircraft', familiarity: 1, everyday: true },
  { value: 1e8, name: '一艘航空母舰', category: 'huge', note: '约 10 万吨', object: 'carrier', familiarity: 1, everyday: true },
  { value: 5.9e9, name: '胡夫金字塔', category: 'huge', note: '约 590 万吨', object: 'pyramid', familiarity: 1, everyday: true },
  { value: 6.7e10, name: '三峡大坝', category: 'huge', note: '混凝土约 6700 万吨', object: 'dam', familiarity: 2 },
  { value: 5.9722e24, name: '地球质量', category: 'cosmic', familiarity: 2, everyday: true },
  { value: 1.98892e30, name: '太阳质量', category: 'cosmic', familiarity: 2 },
]

export const MASS_RULERS: NamedValue[] = [
  { value: 1e-3, name: '1 克' },
  { value: 1, name: '1 千克' },
  { value: 1e3, name: '1 吨' },
]

export const massSystem: ScaleSystem = {
  id: 'mass',
  title: '质量',
  baseUnit: '千克',
  units: MASS_UNITS,
  anchors: MASS_ANCHORS,
  rulers: MASS_RULERS,
  // 一克到地球质量：从一粒米到整个地球
  visible: { min: 1e-3, max: 5.9722e24 },
  // b 默认从一克到一吨
  bridge: { min: 1e-3, max: 1e3 },
  displayUnits: ['ug', 'mg', 'g', 'kg', 't', 'earth-mass'],
  defaultUnit: 'kg',
  categories: MASS_CATEGORIES,
}
