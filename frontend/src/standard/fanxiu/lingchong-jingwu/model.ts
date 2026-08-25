export const LINGCHONG_JINGWU_ACTIVITY_TYPE = 'lingchong-jingwu' as const
export const LINGCHONG_JINGWU_OFFICIAL_NAME = '灵宠竞武'
export const LINGCHONG_JINGWU_USER_ALIAS = '灵武竞宠'

export interface LingchongJingwuResourceItem {
  item_id: number
  name: string
  quality: number
  count: number
  aptitude_gain_by_pet_type: Record<number, number>
  minimum_aptitude_gain: number
  maximum_aptitude_gain: number
}

export interface LingchongJingwuResourceSnapshot {
  activity_id: string
  captured_at: string
  source_kind: 'readonly_backpack_runtime'
  complete: boolean
  items: LingchongJingwuResourceItem[]
  total_count: number
  evidence: Record<string, unknown>
}

export interface LingchongJingwuTaskMilestone {
  task_id: number
  order: number
  name: string
  target: number
  progress: number
  status: number
  finished: boolean
  talent_pill_count: number
  rewards: string[]
}

export interface LingchongJingwuPageModel {
  resourceSnapshot: LingchongJingwuResourceSnapshot | null
  tasks: LingchongJingwuTaskMilestone[]
  personalRankingScope: 'personal'
  planeRankingScope: 'plane'
}
