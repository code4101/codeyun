import api from './index'

export interface FieldModifier {
  dp_max?: number
  charge_seconds?: number
  dp_cost?: number
  duration_seconds?: number
  cooldown_seconds?: number
  range?: number
}

export interface WudaoAttribute {
  id: string
  name: string
  color: string
  modifier: FieldModifier
  levels: [string, string, string]
}

export interface XianfaReward {
  name: string
  cost?: number
  effect: string
}

export interface XianCiShrine {
  id: string
  name: string
  region: string
  immortals: string[]
  kind: string
  unlock: string
  rewards: XianfaReward[]
}

export interface GuigubahuangGuide {
  schema_version: number
  source: {
    game: string
    steam_app_id: number
    build_id: string
    verified_at: string
    kind: string
    files: string[]
  }
  wudao: {
    rules: string[]
    rule_value_levels: Array<{ qualification: number; value: number }>
    base_field: {
      dp_max: number
      monthly_dp: number
      charge_seconds: number
      dp_cost: number
      duration_seconds: number
      cooldown_seconds: number
      range: number
    }
    attributes: WudaoAttribute[]
    example_builds: Array<{ name: string; souls: string[][]; result: string }>
  }
  xian_ci: {
    rules: string[]
    shrines: XianCiShrine[]
  }
}

export async function fetchGuigubahuangGuide(): Promise<GuigubahuangGuide> {
  const response = await api.get('/guigubahuang/guide')
  return response.data
}
