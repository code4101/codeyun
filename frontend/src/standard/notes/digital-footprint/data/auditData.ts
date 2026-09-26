/**
 * 数字足迹自查（self-OSINT）数据模型
 *
 * 用途：把「网上公开可搜到的本人数据」按来源分层展示，用于本人隐私自查与暴露面收敛。
 * 边界：只面向本人自查；不做住址、行踪、亲属等定位类推断，也不接入黑产泄露数据。
 *
 * 分层（各层正交，互不引用）：
 *   seed     标识符——检索的输入（姓名/网名/学校/雇主）
 *   timeline 时间线——由已知锚点倒推的履历，必须标注推证依据与可信度
 *   adapter  采集源——一个数据源一个适配器，独立启停与限速
 *   finding  发现——采集结果，后续由脚本写入，页面只读
 *
 * 演进方向：本文件目前是静态种子数据。接入真实采集后，finding 应落到后端
 * （CODEYUN_DATA_DIR + 鉴权 API），避免个人数据被打进前端 bundle。
 */

/** 可信度：known=本人提供的事实，derived=按学制/时间推证，unknown=待确认 */
export type Confidence = 'known' | 'derived' | 'unknown'

/** 敏感度：high=身份证/住址/账号，medium=雇主/邮箱/手机，low=姓名/学校/网名 */
export type Sensitivity = 'low' | 'medium' | 'high'

export type AdapterStatus = 'todo' | 'running' | 'done' | 'skipped'

export interface SeedIdentifier {
  id: string
  /** 标识符维度，例如 姓名 / 网名 / 学历 / 雇主 */
  kind: string
  value: string
  sensitivity: Sensitivity
  /** 检索价值：能否唯一定位到本人 */
  uniqueness: 'strong' | 'weak'
}

export interface TimelineNode {
  id: string
  stage: string
  org: string
  /** ISO 年月；start 为入学/入职，end 为毕业/离职，空串表示未知 */
  start: string
  end: string
  confidence: Confidence
  /** 推证依据：写清「从哪个已知事实、按什么规则推来」 */
  basis: string
}

export interface CollectionAdapter {
  id: string
  name: string
  dimension: string
  /** 推荐的开源工具；填「—」表示暂无成熟工具 */
  tool: string
  status: AdapterStatus
  note: string
}

export interface Finding {
  id: string
  adapterId: string
  /** 命中的字段，例如 用户名 / 邮箱 / 头像 */
  field: string
  value: string
  sourceUrl?: string
  sensitivity: Sensitivity
  /** 采集日期（ISO）；空表示尚未采集 */
  collectedAt?: string
}

/**
 * 锚点：本人确认过的事实，整条时间线由锚点倒推。
 * 锚点越精确，倒推误差越小；缺失的锚点会直接放大推定误差。
 */
export const anchors = {
  /** 本科入学，整条时间线的主锚点 */
  undergradStart: '2011-09',
  undergradEnd: '2015-06',
  /** 福建县城学制：小学 6 年、初中 3 年、高中 3 年 */
  primaryYears: 6,
  juniorYears: 3,
  seniorYears: 3,
  /** 入学年龄（周岁） */
  schoolEntryAge: 6,
} as const

export const seedIdentifiers: SeedIdentifier[] = [
  { id: 'seed-name', kind: '姓名', value: '陈坤泽', sensitivity: 'low', uniqueness: 'strong' },
  { id: 'seed-nick', kind: '网名', value: 'code4101', sensitivity: 'low', uniqueness: 'strong' },
  { id: 'seed-home', kind: '籍贯', value: '福建 龙岩 连城', sensitivity: 'low', uniqueness: 'weak' },
  { id: 'seed-undergrad', kind: '学历', value: '厦门理工学院 数学专业 本科 2011-2015', sensitivity: 'low', uniqueness: 'strong' },
  { id: 'seed-employer-formal', kind: '雇主', value: '快乐学习教育科技 2017-2020', sensitivity: 'medium', uniqueness: 'strong' },
  { id: 'seed-employer-tower', kind: '雇主', value: '中国铁塔（时间待确认）', sensitivity: 'medium', uniqueness: 'weak' },
]

export const timelineNodes: TimelineNode[] = [
  {
    id: 'tl-primary',
    stage: '小学',
    org: '连城县实验小学',
    start: '1999-09',
    end: '2005-06',
    confidence: 'derived',
    basis: '由初中入学 2005-09 倒推 6 年',
  },
  {
    id: 'tl-junior',
    stage: '初中',
    org: '连城二中',
    start: '2005-09',
    end: '2008-06',
    confidence: 'derived',
    basis: '由高中入学 2008-09 倒推 3 年',
  },
  {
    id: 'tl-senior',
    stage: '高中',
    org: '连城一中',
    start: '2008-09',
    end: '2011-06',
    confidence: 'derived',
    basis: '由本科入学 2011-09 倒推 3 年，同时对应 2011 年高考',
  },
  {
    id: 'tl-undergrad',
    stage: '本科',
    org: '厦门理工学院 数学专业',
    start: '2011-09',
    end: '2015-06',
    confidence: 'known',
    basis: '本人提供；整条时间线的主锚点',
  },
  {
    id: 'tl-tower',
    stage: '工作',
    org: '中国铁塔',
    start: '2015-07',
    end: '',
    confidence: 'unknown',
    basis: '本人提供的存在性事实；起止时间未知，暂以毕业时间为最早边界占位',
  },
  {
    id: 'tl-xmut',
    stage: '工作',
    org: '厦门理工学院',
    start: '',
    end: '',
    confidence: 'unknown',
    basis: '本人提供的存在性事实；时间与用工形式均未知',
  },
  {
    id: 'tl-formal',
    stage: '工作',
    org: '快乐学习教育科技',
    start: '2017-01',
    end: '2020-12',
    confidence: 'known',
    basis: '本人提供，仅精确到年份；履历中唯一具备正式名义的一段',
  },
]

/**
 * 采集源清单。status：
 *   todo     尚未运行
 *   running  正在运行
 *   done     已出结果，对应 findings
 *   skipped  评估后确认不适用
 */
export const collectionAdapters: CollectionAdapter[] = [
  { id: 'ad-nick', name: '用户名枚举', dimension: '网名 code4101 在各站点的注册与占用', tool: 'Maigret / Sherlock / Blackbird', status: 'todo', note: '覆盖数百站点，是关联账号最有效的一步' },
  { id: 'ad-email', name: '邮箱注册枚举', dimension: '常用邮箱注册过哪些服务', tool: 'Holehe', status: 'todo', note: '依赖先确定常用邮箱' },
  { id: 'ad-breach', name: '泄露自查', dimension: '邮箱/手机是否出现在公开泄露事件中', tool: 'Have I Been Pwned / Firefox Monitor', status: 'todo', note: '只用官方查询接口，不落泄露数据' },
  { id: 'ad-search', name: '搜索引擎检索', dimension: '姓名/网名/学校/公司的公开落地页', tool: 'Google / Bing 定向检索', status: 'todo', note: 'site: 定向语法，命中率最高' },
  { id: 'ad-github', name: '代码托管', dimension: 'GitHub/码云 账号与提交邮箱', tool: 'GitHub 站内检索 + commit 作者邮箱', status: 'todo', note: 'commit 元数据常残留真实邮箱' },
  { id: 'ad-alumni', name: '校友网络', dimension: '学校公示、班级群体、竞赛获奖名单', tool: '—', status: 'todo', note: '公示类 PDF 常含完整姓名与班级' },
  { id: 'ad-avatar', name: '头像反查', dimension: '同一头像被复用到哪些站点', tool: 'Yandex Images', status: 'todo', note: '人脸反查能力最强，须限定本人素材' },
  { id: 'ad-meta', name: '元数据', dimension: '本人发布过的图片/文档残留信息', tool: 'ExifTool', status: 'todo', note: '重点看 GPS 与设备型号' },
  { id: 'ad-phone', name: '手机号关联', dimension: '手机号的公开注册与归属地信息', tool: 'PhoneInfoga', status: 'todo', note: '仅用于自查归属地与公开绑定' },
]

/** 采集结果。接入脚本前为空，页面按空态渲染。 */
export const findings: Finding[] = []

/** 由主锚点倒推的出生年区间：学校 + 年级即可定位到年龄区间 */
export const derivedBirthYear = {
  from: 1992,
  to: 1993,
  basis: '入学 1999-09 且 6 周岁入学，生日落在 9 月分界两侧',
} as const
