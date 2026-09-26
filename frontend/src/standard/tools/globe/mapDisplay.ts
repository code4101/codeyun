export const displayLabels = {
  autoRegions: '自动细分行政区', adminBoundaries: '行政区边界', adminNames: '行政区名称',
  countries: '国家标注', regions: '省州标注', cities: '城市标注',
  water: '水域标注', roads: '道路与标注', places: '地点标注',
}
export type DisplayKey = keyof typeof displayLabels
export type MapDisplay = Record<DisplayKey, boolean>
export const defaultMapDisplay: MapDisplay = { autoRegions: true, adminBoundaries: true, adminNames: true, countries: true, regions: true, cities: true, water: true, roads: false, places: true }
export const displayStorageKey = 'codeyun.globe.display.v1'

/** Classify transport geometry separately from labels; administrative borders stay independent. */
export function displayGroup(layer: { id: string; type: string; 'source-layer'?: string }): DisplayKey | null {
  if (layer['source-layer'] === 'transportation') return 'roads'
  if (layer.type !== 'symbol') return null
  if (layer.id.startsWith('label_country_')) return 'countries'
  if (layer.id === 'label_state') return 'regions'
  if (['label_city', 'label_city_capital', 'label_town', 'label_village', 'label_other'].includes(layer.id)) return 'cities'
  if (['water_name', 'waterway'].includes(layer['source-layer'] ?? '')) return 'water'
  if (layer['source-layer'] === 'transportation_name') return 'roads'
  if (['poi', 'aerodrome_label'].includes(layer['source-layer'] ?? '')) return 'places'
  return null
}

export function parseMapDisplay(raw: string | null): MapDisplay {
  const result = { ...defaultMapDisplay }
  try {
    const stored = JSON.parse(raw ?? '{}')
    for (const key of Object.keys(result) as DisplayKey[]) {
      if (typeof stored?.[key] === 'boolean') result[key] = stored[key]
    }
  } catch { /* Old or malformed preferences fall back to the default view. */ }
  return result
}


/** Automatic hierarchy owns province/city naming, including before a country is selected.
 * Basemap settlement labels follow zoom alone and would bypass our hierarchy. */
export function showBasemapLabel(key: DisplayKey, settings: MapDisplay, _hasAdminRegions: boolean): boolean {
  if (settings.autoRegions && (key==='regions' || key==='cities')) return false
  return settings[key]
}
