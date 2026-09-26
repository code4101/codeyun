import { geoContains } from 'd3-geo'
import type { FeatureCollection, Geometry } from 'geojson'
import geometry from './data/countries.json'
import details from './data/countryDetails.json'

export interface CountryProperties {
  name: string
  group: string
  label: [number, number]
}

export interface CountryDetails {
  code: string
  englishName: string
  officialName: string
  continent: string
  region: string
  capital: string[]
  languages: Record<string, string>
  currencies: string[]
  area: number | null
  population: number | null
  populationYear: number
  landlocked: boolean | null
}

/** Shared selection identity for both projections, including grouped regional geometries. */
export const countries = geometry as FeatureCollection<Geometry, CountryProperties>
export const countryDetails: Record<string, CountryDetails> = details
const groups = new Map(countries.features.map(feature => [feature.properties.name, feature.properties.group]))
export function countryGroup(name: string) { return groups.get(name) ?? '' }

/** Natural Earth 1:110m hit testing; ocean clicks clear selection. Not a street-level boundary dataset. */
export function countryAt(longitude: number, latitude: number): string {
  const point: [number, number] = [((longitude + 180) % 360 + 360) % 360 - 180, latitude]
  return countries.features.find(feature => geoContains(feature, point))?.properties.group ?? ''
}

// Snapshot retrieved 2026-09-26. Population and its year: Natural Earth 5.1.2 (public domain).
// Other facts: https://github.com/mledoze/countries (ODbL-1.0; see data/countryDetails.LICENSE.txt).
// Preserve original statistical years rather than presenting snapshot retrieval as census dates.
export const countryDataSources = {
  geography: 'https://www.naturalearthdata.com/downloads/110m-cultural-vectors/110m-admin-0-countries/',
  facts: 'https://github.com/mledoze/countries',
}
