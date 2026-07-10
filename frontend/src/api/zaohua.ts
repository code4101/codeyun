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
  grade_order?: number
  grade_color_index?: number
  grade_color_hex?: string
  price?: number
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

export interface ZaohuaHerbRecipeRef {
  recipe_id: number
  output_item_id: number
  output_name: string
  required_count: number
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
  grade_order: number
  grade_color_index: number
  grade_color_hex: string
  element_id: number
  element_key: string
  element_name: string
  price: number
  lingqi: number
  recipe_count: number
  recipes: ZaohuaHerbRecipeRef[]
  source_evidence: Record<string, string>
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
