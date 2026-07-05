import api from './index'

export interface PokemonTcgCard {
  source: string
  source_url: string
  source_card_slug: string
  set_name: string
  set_slug: string
  official_set_code: string
  official_number: string
  official_total: string
  official_id: string
  official_name: string
  display_title: string
  pokemon_species: string
  hp: string
  color: string
  stage: string
  evolves_from: string
  evolves_into: string
  is_dark: boolean
  attacks_text: string
  weakness_text: string
  resistance_text: string
  retreat_cost: number | string | null
  illustrator_text: string
  rarity: string
  release_date_text: string
  release_meta_text: string
  flavor_text: string
  image_url: string
  local_image_path: string
  image_sha256: string
  image_bytes: number
  raw_text: string
  fetched_at: string
}

export interface PokemonTcgCardListResponse {
  items: PokemonTcgCard[]
  page: number
  page_size: number
  total: number
}

export interface PokemonTcgMeta {
  dataset_id: string
  root: string
  manifest: Record<string, unknown>
  progress: Record<string, unknown>
  card_count: number
  set_counts: Record<string, number>
}

export async function fetchPokemonTcgMeta() {
  const response = await api.get<PokemonTcgMeta>('/pokemon-tcg/meta')
  return response.data
}

export async function fetchPokemonTcgCards(params: {
  q?: string
  set?: string
  page?: number
  page_size?: number
}) {
  const response = await api.get<PokemonTcgCardListResponse>('/pokemon-tcg/cards', { params })
  return response.data
}

export function pokemonTcgImageUrl(card: PokemonTcgCard) {
  if (!card.local_image_path) return card.image_url
  const imagePath = card.local_image_path.replace(/^images[\\/]/, '')
  return `/api/pokemon-tcg/images/${imagePath}`
}
