import api from './index'

export interface ZaohuaAlchemyElementLimit {
  element: string
  label: string
  value: number
}

export interface ZaohuaAlchemyItem {
  item_id: number
  name: string
  count?: number
  description?: string
  effect_description?: string
  use_effect?: string
  icon_path?: string
  icon_url?: string
  grade_id?: number
  grade_name?: string
  grade_rank?: number
  grade_order?: number
  grade_color_index?: number
  grade_color_hex?: string
  price?: number
  effect_text?: string
  augment?: number
  efficacy?: number
  add_drug_tolerance?: number
  drug_max?: number
  crafting_attributes?: ZaohuaAlchemyElementLimit[]
}

export interface ZaohuaAlchemyStateRule {
  state_id: number
  name: string
  pool_type?: string
  state_type?: string
  area?: string
  calculate_type?: string
  target1?: string
  target2?: string
  relation?: string
  base_effect?: string
}

export interface ZaohuaAlchemyRecipe {
  recipe_id: number
  source_build_id: string
  name: string
  technique: string
  output: ZaohuaAlchemyItem & {
    count: number
  }
  cost_days: number
  attr_limits: ZaohuaAlchemyElementLimit[]
  example_items: ZaohuaAlchemyItem[]
  state_rules: ZaohuaAlchemyStateRule[]
  source_evidence: Record<string, string>
  content_hash: string
}

export interface ZaohuaAlchemyMeta {
  recipe_count: number
  build_ids: string[]
  grades: Array<{
    name: string
    count: number
    grade_id: number
    order: number
    color_index: number
    color_hex: string
  }>
  storage: string
}

export interface ZaohuaAlchemyRecipePage {
  items: ZaohuaAlchemyRecipe[]
  page: number
  page_size: number
  total: number
}

export const fetchZaohuaAlchemyMeta = async (): Promise<ZaohuaAlchemyMeta> => {
  const response = await api.get('/zaohua/alchemy/meta')
  return response.data
}

export const fetchZaohuaAlchemyRecipes = async (params: {
  q?: string
  grade?: string
  sort_by?: 'number' | 'grade'
  sort_order?: 'asc' | 'desc'
  page?: number
  page_size?: number
}): Promise<ZaohuaAlchemyRecipePage> => {
  const response = await api.get('/zaohua/alchemy/recipes', { params })
  return response.data
}

export const fetchZaohuaAlchemyRecipe = async (recipeId: number): Promise<ZaohuaAlchemyRecipe> => {
  const response = await api.get(`/zaohua/alchemy/recipes/${recipeId}`)
  return response.data
}

export interface ZaohuaAlchemySolutionHerb {
  item_id: number
  name: string
  side: 'yang' | 'yin'
  count: number
  unit_price: number
}

export type ZaohuaAlchemyValueMetric = 'output_input_ratio' | 'net_profit' | 'profit_rate'

export interface ZaohuaAlchemySolution {
  rank: number
  grade_histogram: number[]
  grade_sequence: number[]
  grade_groups: Array<{ grade_rank: number; count: number }>
  max_herb_grade: number
  occupied_cells: number
  ratio: number | null
  output_input_ratio: number | null
  net_profit: number
  profit_rate: number | null
  cost: number
  base_yield: number
  rule_bonus: number
  final_yield: number
  total_value: number
  rule_supported: boolean
  herbs: ZaohuaAlchemySolutionHerb[]
  placements: Array<{
    item_id: number
    name: string
    side: 'yang' | 'yin'
    x: number
    y: number
    rotation: number
    cells: Array<[number, number]>
    shape_draw_id: number
    shape_width: number
    shape_height: number
    shape_image_url: string
  }>
}

export interface ZaohuaAlchemySolveResult {
  recipe_id: number
  furnace: ZaohuaFurnace
  solutions: ZaohuaAlchemySolution[]
  target_vector: Record<string, number>
  candidate_count: number
  search_nodes: number
  packing_nodes: number
  pruned_unreachable: number
  pruned_cell_capacity: number
  seed_solution_found: boolean
  exhaustive: boolean
  search_mode: 'grade_descent'
  vector_mode: 'abc_bounded'
  objective: 'grade_descent'
  duration: number
  has_more: boolean
  solution_count: number
  available_herbs: Array<{
    item_id: number
    name: string
    price: number
    icon_path: string
    icon_url: string
  }>
  excluded_item_ids: number[]
}

export const solveZaohuaAlchemy = async (
  recipeId: number,
  payload: {
    furnace_item_id: number
    limit?: number
    excluded_item_ids?: number[]
    sort_metrics?: ZaohuaAlchemyValueMetric[]
  },
): Promise<ZaohuaAlchemySolveResult> => {
  const response = await api.post(`/zaohua/alchemy/recipes/${recipeId}/solve`, payload)
  return response.data
}

export interface ZaohuaHerbRecipeRef {
  recipe_id: number
  output_item_id: number
  output_name: string
  required_count: number
}

export interface ZaohuaHerbCraftingAttribute {
  element: string
  label: string
  value: number
}

export interface ZaohuaHerb {
  item_id: number
  source_build_id: string
  display_order: number
  name: string
  description: string
  effect_description: string
  icon_path: string
  icon_url: string
  grade_id: number
  grade_name: string
  grade_rank: number
  grade_order: number
  grade_color_index: number
  grade_color_hex: string
  element_id: number
  element_key: string
  element_name: string
  price: number
  lingqi: number
  crafting_attributes: ZaohuaHerbCraftingAttribute[]
  recipe_count: number
  recipes: ZaohuaHerbRecipeRef[]
  shape?: {
    draw_id: number
    name: string
    path: string
    image_url: string
    width: number
    height: number
    cells: Array<[number, number]>
  } | null
  source_evidence: Record<string, unknown>
  content_hash: string
}

export interface ZaohuaHerbMeta {
  herb_count: number
  build_ids: string[]
  grades: Array<{
    name: string
    count: number
    grade_id: number
    order: number
    color_index: number
    color_hex: string
  }>
  elements: Array<{
    key: string
    name: string
    count: number
  }>
  storage: string
}

export interface ZaohuaHerbPage {
  items: ZaohuaHerb[]
  page: number
  page_size: number
  total: number
}

export const fetchZaohuaHerbMeta = async (): Promise<ZaohuaHerbMeta> => {
  const response = await api.get('/zaohua/herbs/meta')
  return response.data
}

export const fetchZaohuaHerbs = async (params: {
  q?: string
  grade?: string
  element?: string
  sort_by?: 'number' | 'grade'
  sort_order?: 'asc' | 'desc'
  page?: number
  page_size?: number
}): Promise<ZaohuaHerbPage> => {
  const response = await api.get('/zaohua/herbs', { params })
  return response.data
}

export const fetchZaohuaHerb = async (itemId: number): Promise<ZaohuaHerb> => {
  const response = await api.get(`/zaohua/herbs/${itemId}`)
  return response.data
}

export interface ZaohuaFurnace {
  item_id: number
  display_order: number
  name: string
  description: string
  effect_description: string
  icon_path: string
  icon_url: string
  grade_id: number
  grade_name: string
  grade_rank: number
  grade_color_hex: string
  element_id: number
  element_key: string
  element_name: string
  price: number
  drug_quality: number
  add_drug_tolerance: number
  yang_grid_size: { width: number; height: number }
  yin_grid_size: { width: number; height: number }
  crafting_effect: Record<string, unknown>
  source_evidence: Record<string, unknown>
  content_hash: string
}

export interface ZaohuaFurnaceMeta {
  furnace_count: number
  build_ids: string[]
  grades: Array<{ name: string; count: number; grade_id: number; order: number; color_hex: string }>
  elements: Array<{ key: string; name: string; count: number }>
  storage: string
}

export interface ZaohuaFurnacePage {
  items: ZaohuaFurnace[]
  page: number
  page_size: number
  total: number
}

export const fetchZaohuaFurnaceMeta = async (): Promise<ZaohuaFurnaceMeta> => {
  const response = await api.get('/zaohua/furnaces/meta')
  return response.data
}

export const fetchZaohuaFurnaces = async (params: {
  q?: string; grade?: string; element?: string
  sort_by?: 'number' | 'grade'; sort_order?: 'asc' | 'desc'
  page?: number; page_size?: number
}): Promise<ZaohuaFurnacePage> => {
  const response = await api.get('/zaohua/furnaces', { params })
  return response.data
}

export const fetchZaohuaFurnace = async (itemId: number): Promise<ZaohuaFurnace> => {
  const response = await api.get(`/zaohua/furnaces/${itemId}`)
  return response.data
}

export interface ZaohuaPastureBuilding {
  build_id: number
  name: string
  description: string
  type: number
  size: string
  effect_range_type: number
  effect: string
  effect_params: string[]
  path: string
  image_url: string
  source_evidence: Record<string, string>
}

export interface ZaohuaPastureMeta {
  source: { steam_build_id?: string }
  stats: { building_count: number; image_count: number }
  model: { plot_name: string; default_plot_count: number; adjacency: 'orthogonal'; code_evidence: string[] }
  buildings: ZaohuaPastureBuilding[]
}

export const fetchZaohuaPastureMeta = async (): Promise<ZaohuaPastureMeta> => {
  const response = await api.get('/zaohua/pasture/meta')
  return response.data
}

export interface ZaohuaPastureSolutionCell {
  index: number
  x: number
  y: number
  kind: 'plot' | 'building'
  building_id?: number
  productive?: boolean
  speed?: number
  yield?: number
  speed_count?: number
  yield_count?: number
  coefficient?: number
  output?: number
}

export interface ZaohuaPastureSolution {
  plot_count: number
  shape: Array<{ x: number; y: number }>
  objective: 'herb_output_per_time' | 'total_value'
  base_output: number
  equivalent_output: number
  total_value: number
  gain: number
  herb_count: number
  pool_count: number
  herb_value: number
  fish_value: number
  production_mode: 'free' | 'exact' | 'target_ratio'
  ratio_deviation: number
  used_building_ids: number[]
  cells: ZaohuaPastureSolutionCell[]
  exact: boolean
  search?: { shape_candidates: number; layout_candidates: number; method: string }
}

export const solveZaohuaPasture = async (payload: {
  plot_count: number
  production_mode: 'free' | 'exact' | 'target_ratio'
  herb_count: number
  pool_count: number
  herb_weight?: number
  fish_weight?: number
  enabled_building_ids: number[]
  building_counts: Record<number, number>
  exact_building_counts?: boolean
  special_cell_count?: number
}): Promise<ZaohuaPastureSolution> => {
  const response = await api.post('/zaohua/pasture/solve', payload)
  return response.data
}
