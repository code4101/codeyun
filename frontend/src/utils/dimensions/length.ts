import type { NamedValue, ScaleAnchor, ScaleCategory, ScaleSystem, ScaleUnit } from '@/utils/scale'

/** 地球赤道周长，既是长度的最大参照物，也是 c、d 的上限。 */
export const EARTH_EQUATOR_LENGTH = 4.0075017e7

const AU = 1.495978707e11
const LIGHT_YEAR = 9.4607304725808e15
const PARSEC = 3.0856775814913673e16

/**
 * 长度单位表，覆盖 pint 里 meter 系的长度单位：SI 前缀整条梯子、英美/测量制、
 * 天文距离、中国市制。写成哪一段不重要，用的时候一律按大小排序。
 */
export const LENGTH_UNITS: ScaleUnit[] = [
  // 公制（含完整 SI 前缀）
  { id: 'ym', name: '幺米', symbol: 'ym', factor: 1e-24 },
  { id: 'zm', name: '仄米', symbol: 'zm', factor: 1e-21 },
  { id: 'am', name: '阿米', symbol: 'am', factor: 1e-18 },
  { id: 'fm', name: '飞米', symbol: 'fm', factor: 1e-15 },
  { id: 'pm', name: '皮米', symbol: 'pm', factor: 1e-12 },
  { id: 'angstrom', name: '埃', symbol: 'Å', factor: 1e-10, aliases: ['A', 'ångström'] },
  { id: 'nm', name: '纳米', symbol: 'nm', factor: 1e-9 },
  { id: 'um', name: '微米', symbol: 'µm', factor: 1e-6, aliases: ['μm', 'um', 'micron'] },
  { id: 'mm', name: '毫米', symbol: 'mm', factor: 1e-3 },
  { id: 'cm', name: '厘米', symbol: 'cm', factor: 1e-2, aliases: ['公分'] },
  { id: 'dm', name: '分米', symbol: 'dm', factor: 1e-1 },
  { id: 'm', name: '米', symbol: 'm', factor: 1, aliases: ['公尺'] },
  { id: 'dam', name: '十米', symbol: 'dam', factor: 1e1 },
  { id: 'hm', name: '百米', symbol: 'hm', factor: 1e2 },
  { id: 'km', name: '千米', symbol: 'km', factor: 1e3, aliases: ['公里'] },
  { id: 'Mm', name: '兆米', symbol: 'Mm', factor: 1e6 },
  { id: 'Gm', name: '吉米', symbol: 'Gm', factor: 1e9 },
  { id: 'Tm', name: '太米', symbol: 'Tm', factor: 1e12 },
  { id: 'Pm', name: '拍米', symbol: 'Pm', factor: 1e15 },
  { id: 'Em', name: '艾米', symbol: 'Em', factor: 1e18 },
  { id: 'Zm', name: '泽米', symbol: 'Zm', factor: 1e21 },
  { id: 'Ym', name: '尧米', symbol: 'Ym', factor: 1e24 },

  // 英美制与英制测量制
  { id: 'thou', name: '密尔', symbol: 'thou', factor: 2.54e-5, aliases: ['mil'] },
  { id: 'point', name: '磅点', symbol: 'pt', factor: 0.0254 / 72 },
  { id: 'pica', name: '派卡', symbol: 'pica', factor: 0.0254 / 6 },
  { id: 'in', name: '英寸', symbol: 'in', factor: 0.0254, aliases: ['inch'] },
  { id: 'hand', name: '掌宽', symbol: 'hand', factor: 0.1016 },
  { id: 'ft', name: '英尺', symbol: 'ft', factor: 0.3048, aliases: ['foot'] },
  { id: 'survey_ft', name: '测量英尺', symbol: 'survey_ft', factor: 1200 / 3937 },
  { id: 'yd', name: '码', symbol: 'yd', factor: 0.9144, aliases: ['yard'] },
  { id: 'fathom', name: '英寻', symbol: 'ftm', factor: 1.8288 },
  { id: 'rod', name: '杆', symbol: 'rod', factor: 5.0292, aliases: ['pole', 'perch'] },
  { id: 'chain', name: '测链', symbol: 'ch', factor: 20.1168 },
  { id: 'furlong', name: '浪', symbol: 'fur', factor: 201.168 },
  { id: 'mi', name: '英里', symbol: 'mi', factor: 1609.344 },
  { id: 'survey_mi', name: '测量英里', symbol: 'survey_mi', factor: (5280 * 1200) / 3937 },
  { id: 'nmi', name: '海里', symbol: 'nmi', factor: 1852 },
  { id: 'league', name: '里格', symbol: 'league', factor: 4828.032 },

  // 中国市制
  { id: 'cun', name: '寸', symbol: '寸', factor: 1 / 30 },
  { id: 'chi', name: '尺', symbol: '尺', factor: 1 / 3 },
  { id: 'zhang', name: '丈', symbol: '丈', factor: 10 / 3 },
  { id: 'li', name: '里', symbol: '里', factor: 500 },

  // 天文
  { id: 'ls', name: '光秒', symbol: 'ls', factor: 2.99792458e8 },
  { id: 'lmin', name: '光分', symbol: 'lmin', factor: 2.99792458e8 * 60 },
  { id: 'lh', name: '光时', symbol: 'lh', factor: 2.99792458e8 * 3600 },
  { id: 'ld', name: '光日', symbol: 'ld', factor: 2.99792458e8 * 86400 },
  { id: 'au', name: '天文单位', symbol: 'AU', factor: AU, aliases: ['au', 'astronomical_unit'] },
  { id: 'ly', name: '光年', symbol: 'ly', factor: LIGHT_YEAR, aliases: ['light_year'] },
  { id: 'pc', name: '秒差距', symbol: 'pc', factor: PARSEC, aliases: ['parsec'] },
  { id: 'kpc', name: '千秒差距', symbol: 'kpc', factor: PARSEC * 1e3 },
  { id: 'mpc', name: '百万秒差距', symbol: 'Mpc', factor: PARSEC * 1e6 },
]

export const LENGTH_ANCHOR_CATEGORIES: ScaleCategory[] = [
  { id: 'quantum', title: '量子', color: '#8b5cf6' },
  { id: 'atomic', title: '原子', color: '#6366f1' },
  { id: 'molecular', title: '分子与病毒', color: '#0ea5e9' },
  { id: 'micro', title: '微观生物', color: '#14b8a6' },
  { id: 'daily', title: '日常', color: '#22c55e' },
  { id: 'geo', title: '地球', color: '#f59e0b' },
  { id: 'astro', title: '天文', color: '#ef4444' },
  { id: 'cosmic', title: '宇宙', color: '#a0224a' },
]

/**
 * 长度的常识阶梯。familiarity：1 = 伸手可及或地理常识；2 = 需要一点科普；3 = 专业尺度。
 * everyday：熟悉到能当比喻里的参照物——b 可以稍小，c、d 还要求 ≥ 1 毫米且不出地球。
 * object：同一个东西的不同量（半径/直径/轨道）共用同一 id，避免自我参照。
 */
export const LENGTH_ANCHORS: ScaleAnchor[] = [
  { value: 1.616255e-35, name: '普朗克长度', category: 'quantum', note: '现有物理理论中长度的下限', source: 'CODATA 2018', familiarity: 3, abstract: true },
  { value: 1e-18, name: '电子尺度上限', category: 'quantum', note: '至今未测到电子内部结构', familiarity: 3, abstract: true },
  { value: 8.414e-16, name: '质子电荷半径', category: 'quantum', note: '0.8414 fm', source: 'CODATA 2018', object: 'proton', familiarity: 3 },
  { value: 1.6828e-15, name: '质子直径', category: 'quantum', object: 'proton', familiarity: 3 },
  { value: 5.2917721e-11, name: '氢原子玻尔半径', category: 'atomic', note: '0.0529 nm', source: 'CODATA 2018', object: 'hydrogen', familiarity: 3 },
  { value: 1.06e-10, name: '氢原子直径', category: 'atomic', object: 'hydrogen', familiarity: 2 },
  { value: 1.42e-10, name: '石墨烯晶格常数', category: 'atomic', note: '0.142 nm', object: 'graphene', familiarity: 3 },
  { value: 1.54e-10, name: '碳碳单键键长', category: 'atomic', object: 'cc-bond', familiarity: 3 },
  { value: 2e-9, name: 'DNA 双螺旋直径', category: 'molecular', object: 'dna', familiarity: 2 },
  { value: 1e-8, name: '最小蛋白质分子', category: 'molecular', object: 'protein', familiarity: 2 },
  { value: 1.2e-7, name: '流感病毒直径', category: 'molecular', object: 'virus', familiarity: 2 },
  { value: 2e-6, name: '大肠杆菌体长', category: 'micro', object: 'ecoli', familiarity: 2 },
  { value: 7e-6, name: '人类红血球直径', category: 'micro', object: 'rbc', familiarity: 2 },
  { value: 2.5e-4, name: '尘螨体长', category: 'micro', object: 'mite', familiarity: 2 },
  { value: 7e-5, name: '人类头发直径', category: 'micro', object: 'hair', familiarity: 1, everyday: true, note: '约 0.07 毫米，只能当 b' },
  { value: 1e-4, name: '一张纸的厚度', category: 'daily', object: 'paper', familiarity: 1, everyday: true, note: '约 0.1 毫米，只能当 b' },
  { value: 5e-4, name: '食盐颗粒', category: 'daily', object: 'salt', familiarity: 1, everyday: true, note: '约 0.5 毫米，只能当 b' },
  { value: 3e-3, name: '蚂蚁体长', category: 'daily', object: 'ant', familiarity: 1, everyday: true },
  { value: 5e-3, name: '米粒长度', category: 'daily', object: 'rice', familiarity: 1, everyday: true },
  { value: 2.5e-2, name: '硬币直径', category: 'daily', object: 'coin', familiarity: 1, everyday: true },
  { value: 4e-2, name: '乒乓球直径', category: 'daily', object: 'pingpong', familiarity: 1, everyday: true },
  { value: 2.4e-1, name: '篮球直径', category: 'daily', object: 'basketball', familiarity: 1, everyday: true },
  { value: 1.8e-1, name: '成年人手掌长', category: 'daily', object: 'palm', familiarity: 1, everyday: true },
  { value: 1.7, name: '成年人身高', category: 'daily', object: 'human', familiarity: 1, everyday: true },
  { value: 4.5, name: '一辆汽车长度', category: 'daily', object: 'car', familiarity: 1, everyday: true },
  { value: 3e1, name: '蓝鲸体长', category: 'daily', object: 'whale', familiarity: 1, everyday: true },
  { value: 1.05e2, name: '足球场长度', category: 'daily', object: 'pitch', familiarity: 1, everyday: true },
  { value: 1.5e-1, name: '一支笔的长度', category: 'daily', object: 'pen', familiarity: 1, everyday: true },
  { value: 2.97e-1, name: '一张 A4 纸的长边', category: 'daily', object: 'a4', familiarity: 1, everyday: true },
  { value: 2, name: '一扇门的高度', category: 'daily', object: 'door', familiarity: 1, everyday: true },
  { value: 12, name: '一辆公交车的长度', category: 'daily', object: 'bus', familiarity: 1, everyday: true },
  { value: 50, name: '标准泳池的长度', category: 'daily', object: 'pool', familiarity: 1, everyday: true },
  { value: 330, name: '埃菲尔铁塔的高度', category: 'geo', object: 'eiffel', familiarity: 1, everyday: true },
  { value: 3500, name: '机场跑道的长度', category: 'geo', object: 'runway', familiarity: 1, everyday: true },
  { value: 8848.86, name: '珠穆朗玛峰', category: 'geo', note: '海拔 8848.86 米', object: 'everest', familiarity: 1, everyday: true },
  { value: 10994, name: '马里亚纳海沟', category: 'geo', note: '最深约 10994 米', object: 'mariana', familiarity: 1, everyday: true },
  { value: 4.2195e4, name: '马拉松全程', category: 'geo', note: '42.195 公里', object: 'marathon', familiarity: 1, everyday: true },
  { value: 5.5e4, name: '港珠澳大桥', category: 'geo', note: '全长约 55 公里', object: 'hzmb', familiarity: 1, everyday: true },
  { value: 1.3e5, name: '台湾海峡最窄处', category: 'geo', note: '约 130 公里', object: 'taiwan-strait', familiarity: 1, everyday: true },
  { value: 1.794e6, name: '京杭大运河', category: 'geo', note: '全长约 1794 公里，世界最长的人工运河', object: 'canal', familiarity: 1, everyday: true },
  { value: 6.371e6, name: '地球半径', category: 'geo', note: '平均约 6371 公里', object: 'earth-radius', familiarity: 1, everyday: true },
  { value: 8.8518e6, name: '长城', category: 'geo', note: '明长城约 8852 公里', object: 'greatwall', familiarity: 1, everyday: true },
  { value: 1.2742e7, name: '地球直径', category: 'geo', object: 'earth', familiarity: 1, everyday: true },
  { value: EARTH_EQUATOR_LENGTH, name: '地球赤道周长', category: 'geo', object: 'earth', familiarity: 1, everyday: true },
  { value: 3.844e8, name: '地月平均距离', category: 'astro', object: 'moon-orbit', familiarity: 1 },
  { value: 1.3927e9, name: '太阳直径', category: 'astro', object: 'sun', familiarity: 1 },
  { value: AU, name: '日地距离', category: 'astro', note: '1 天文单位', object: 'earth-orbit', familiarity: 1 },
  { value: 7.785e11, name: '木星轨道半径', category: 'astro', object: 'jupiter-orbit', familiarity: 3 },
  { value: 1.434e12, name: '土星轨道半径', category: 'astro', object: 'saturn-orbit', familiarity: 3 },
  { value: 2.8725e12, name: '天王星轨道半径', category: 'astro', object: 'uranus-orbit', familiarity: 3 },
  { value: 4.495e12, name: '海王星轨道半径', category: 'astro', object: 'neptune-orbit', familiarity: 3 },
  { value: 5.9e12, name: '冥王星平均轨道半径', category: 'astro', object: 'pluto-orbit', familiarity: 2 },
  { value: LIGHT_YEAR, name: '光年', category: 'astro', object: 'lightyear', familiarity: 1 },
  { value: 4.0e16, name: '比邻星距离', category: 'astro', object: 'proxima', familiarity: 2 },
  { value: 9.46e20, name: '银河系直径', category: 'cosmic', object: 'milkyway', familiarity: 2 },
  { value: 2.4e22, name: '仙女座星系距离', category: 'cosmic', object: 'andromeda', familiarity: 2 },
  { value: 1.04e24, name: '室女座星系团尺度', category: 'cosmic', object: 'virgo', familiarity: 3 },
  { value: 5.2e24, name: '拉尼亚凯亚超星系团', category: 'cosmic', object: 'laniakea', familiarity: 3 },
  { value: 8.8e26, name: '可观测宇宙直径', category: 'cosmic', object: 'universe', familiarity: 2 },
]

/** 单位尺：把「1 米」「1 公里」这类单位本身也当作比喻里的一根参照物。 */
export const LENGTH_RULERS: NamedValue[] = [
  { value: 1e-3, name: '1 毫米' },
  { value: 1e-2, name: '1 厘米' },
  { value: 1, name: '1 米' },
  { value: 1e3, name: '1 公里' },
]

export const lengthSystem: ScaleSystem = {
  id: 'length',
  title: '长度',
  baseUnit: '米',
  units: LENGTH_UNITS,
  anchors: LENGTH_ANCHORS,
  rulers: LENGTH_RULERS,
  // 看得见的东西：从 1 毫米到地球赤道周长
  visible: { min: 1e-3, max: EARTH_EQUATOR_LENGTH },
  // b 默认从一粒米到一段城市距离
  bridge: { min: 1e-3, max: 1e3 },
  displayUnits: ['am', 'fm', 'pm', 'angstrom', 'nm', 'um', 'mm', 'cm', 'm', 'km', 'au', 'ly', 'kpc', 'mpc'],
  defaultUnit: 'nm',
  categories: LENGTH_ANCHOR_CATEGORIES,
}
