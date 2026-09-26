import { geoArea } from 'd3-geo'
import type { Feature, FeatureCollection, MultiPolygon, Polygon, Position } from 'geojson'

export interface AdminProperties {
  code: string; name: string; level: number; parent: string; children: boolean; label: [number, number]
}
export type AdminFeature = Feature<Polygon | MultiPolygon, AdminProperties>
export interface AdminViewport {
  width: number; height: number
  unproject: (x: number, y: number) => [number, number] | null
}
export const adminSource = 'https://datav.aliyun.com/portal/school/atlas/area_'
const cache = new Map<string, Promise<AdminFeature[]>>()

/** DataV/AMap coordinates are GCJ-02; normalize once before overlaying the WGS84 basemap. */
export function toWgs84([lng, lat]: Position): [number, number] {
  if (lng! < 72.004 || lng! > 137.8347 || lat! < 0.8293 || lat! > 55.8271) return [lng!, lat!]
  const x = lng! - 105, y = lat! - 35, pi = Math.PI
  let dLat = -100 + 2*x + 3*y + 0.2*y*y + 0.1*x*y + 0.2*Math.sqrt(Math.abs(x))
  let dLng = 300 + x + 2*y + 0.1*x*x + 0.1*x*y + 0.1*Math.sqrt(Math.abs(x))
  const common = (20*Math.sin(6*x*pi) + 20*Math.sin(2*x*pi))*2/3
  dLat += common + (20*Math.sin(y*pi) + 40*Math.sin(y*pi/3))*2/3 + (160*Math.sin(y*pi/12) + 320*Math.sin(y*pi/30))*2/3
  dLng += common + (20*Math.sin(x*pi) + 40*Math.sin(x*pi/3))*2/3 + (150*Math.sin(x*pi/12) + 300*Math.sin(x*pi/30))*2/3
  const rad = lat!*pi/180, magic = 1 - 0.006693421622965943*Math.sin(rad)**2
  dLat = dLat*180 / ((6378245*(1-0.006693421622965943)/(magic*Math.sqrt(magic)))*pi)
  dLng = dLng*180 / ((6378245/Math.sqrt(magic))*Math.cos(rad)*pi)
  return [lng!-dLng, lat!-dLat]
}

export function normalizeRegions(data: FeatureCollection): AdminFeature[] {
  return data.features.flatMap(feature => {
    const p = feature.properties
    // Exclude decorative maritime features (100000_JD), never count them as provinces.
    if (!p || !/^\d{6}$/.test(String(p.adcode)) || !['province', 'city', 'district'].includes(p.level)
      || !['Polygon', 'MultiPolygon'].includes(feature.geometry.type)) return []
    const geometry = feature.geometry as Polygon | MultiPolygon
    const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates
    const coordinates = polygons.map(polygon => {
      const rings = polygon.map(ring => ring.map(toWgs84))
      // D3 spherical paths expect small polygons clockwise; MapLibre accepts either winding.
      if (geoArea({ type: 'Polygon', coordinates: rings }) > Math.PI * 2) rings.forEach(ring => ring.reverse())
      return rings
    })
    return [{ type: 'Feature', geometry: { type: 'MultiPolygon', coordinates }, properties: {
      code: String(p.adcode), name: p.name, level: { province: 1, city: 2, district: 3 }[p.level as 'province' | 'city' | 'district'],
      parent: String(p.parent?.adcode ?? ''), children: p.level !== 'district' && Number(p.childrenNum) > 0,
      label: toWgs84(p.centroid ?? p.center),
    } } as AdminFeature]
  })
}

export function loadAdminChildren(code: string): Promise<AdminFeature[]> {
  const existing = cache.get(code)
  if (existing) return existing
  if (!/^\d{6}$/.test(code)) return Promise.reject(new Error('行政区代码无效'))
  const request = code === '100000' ? import('./data/chinaProvinces.json').then(module => normalizeRegions(module.default as FeatureCollection)) :
    fetch(`https://geo.datav.aliyun.com/areas_v3/bound/${code}_full.json`, { signal: AbortSignal.timeout(12000) })
      .then(async response => {
        if (!response.ok) throw new Error('行政区边界加载失败')
        const data = await response.json()
        if (data.type !== 'FeatureCollection' || !Array.isArray(data.features)) throw new Error('行政区边界格式异常')
        return normalizeRegions(data)
      })
  cache.set(code, request)
  request.catch(() => cache.delete(code))
  return request
}

const boundsCache = new WeakMap<AdminFeature, number[]>()
export function regionBounds(feature: AdminFeature): number[] {
  const polygons = feature.geometry.type === 'Polygon' ? [feature.geometry.coordinates] : feature.geometry.coordinates
  let bounds = boundsCache.get(feature)
  if (!bounds) {
    bounds = [Infinity, Infinity, -Infinity, -Infinity]
    for (const polygon of polygons) for (const ring of polygon) for (const p of ring) {
      bounds[0] = Math.min(bounds[0]!, p[0]!); bounds[1] = Math.min(bounds[1]!, p[1]!)
      bounds[2] = Math.max(bounds[2]!, p[0]!); bounds[3] = Math.max(bounds[3]!, p[1]!)
    }
    boundsCache.set(feature, bounds)
  }
  return bounds
}

/** Conservative padded envelope: keep tiny regions even when no grid point hits them.
 * A dateline-crossing view keeps all candidates rather than dropping valid geometry. */
export function visibleRegions(regions: AdminFeature[], samples: Array<[number,number] | null>): AdminFeature[] {
  const points=samples.filter((p): p is [number,number]=>p!==null)
  if (!points.length) return []
  const west=Math.min(...points.map(p=>p[0])), east=Math.max(...points.map(p=>p[0]))
  const south=Math.min(...points.map(p=>p[1])), north=Math.max(...points.map(p=>p[1]))
  if (east-west>180) return regions
  const dx=Math.max((east-west)*.2,.01), dy=Math.max((north-south)*.2,.01)
  return regions.filter(feature=>{
    const b=regionBounds(feature)
    return b[2]!>=west-dx && b[0]!<=east+dx && b[3]!>=south-dy && b[1]!<=north+dy
  })
}

/** Point-in-polygon includes holes and ignores winding; suitable for China's local geometry. */
export function containsRegion(feature: AdminFeature, [x, y]: [number, number]): boolean {
  const polygons = feature.geometry.type === 'Polygon' ? [feature.geometry.coordinates] : feature.geometry.coordinates
  const bounds=regionBounds(feature)
  if (x < bounds[0]! || y < bounds[1]! || x > bounds[2]! || y > bounds[3]!) return false
  const inRing = (ring: Position[]) => {
    let inside = false
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const a = ring[i]!, b = ring[j]!
      if ((a[1]! > y) !== (b[1]! > y) && x < (b[0]!-a[0]!)*(y-a[1]!)/(b[1]!-a[1]!)+a[0]!) inside = !inside
    }
    return inside
  }
  return polygons.some(polygon => inRing(polygon[0]!) && !polygon.slice(1).some(inRing))
}

export function viewportSamples(view: AdminViewport): Array<[number, number] | null> {
  const points: Array<[number, number] | null> = []
  for (let y = 0; y < 20; y++) for (let x = 0; x < 20; x++) points.push(view.unproject((x+.5)*view.width/20, (y+.5)*view.height/20))
  return points
}
/** Screen footprint, not solid polygon area: a country spanning 1/3 of both axes
 * should already show its subdivisions even though its irregular land area is much smaller.
 * Only visible samples count, so off-screen/back-side geometry cannot trigger refinement.
 */
export function screenCoverage(samples: Array<[number, number] | null>, contains: (point: [number, number]) => boolean): number {
  let left = 20, right = -1, top = 20, bottom = -1, count = 0
  samples.forEach((point, index) => {
    if (!point || !contains(point)) return
    const x = index % 20, y = Math.floor(index / 20)
    left = Math.min(left,x); right = Math.max(right,x)
    top = Math.min(top,y); bottom = Math.max(bottom,y)
    count++
  })
  if (count < 4) return 0
  return Math.min((right-left+1)/20, (bottom-top+1)/20)
}
export function shouldExpand(coverage: number, expanded: boolean) { return coverage >= (expanded ? 0.20 : 0.35) }


/** Country overview uses its footprint; deeper levels require a majority of actual
 * viewport area. Long, narrow provinces must not trigger detail just by spanning the view.
 * Both thresholds exceed half, so neighbouring provinces cannot expand together. */
export function shouldExpandSubdivision(samples: Array<[number,number] | null>, region: AdminFeature, expanded: boolean): boolean {
  if (!samples.length) return false
  const occupied=samples.reduce((count,point)=>count+(point && containsRegion(region,point) ? 1 : 0),0)
  return occupied/samples.length >= (expanded ? .52 : .60)
}
