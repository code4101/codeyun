import { test } from 'node:test'
import assert from 'node:assert/strict'
import { geoArea, geoEqualEarth } from 'd3-geo'
import { containsRegion, loadAdminChildren, normalizeRegions, screenCoverage, shouldExpand, viewportSamples, toWgs84 } from './adminRegions'
import type { FeatureCollection } from 'geojson'

test('province seed contains exactly 34 real regions with usable spherical polygons', async () => {
  const regions = await loadAdminChildren('100000')
  assert.equal(regions.length, 34)
  assert.equal(new Set(regions.map(r => r.properties.code)).size, 34)
  for (const code of ['110000','330000','710000','810000','820000']) assert.ok(regions.some(r => r.properties.code === code))
  assert.ok(regions.every(r => geoArea(r) < Math.PI * 2))
  const zhejiang = regions.find(r => r.properties.code === '330000')!
  assert.equal(containsRegion(zhejiang, [120.15,30.27]), true)
  assert.equal(containsRegion(zhejiang, [116.4,39.9]), false)
})

test('viewport occupancy counts visible pixels rather than geographic bounding boxes', async () => {
  const regions = await loadAdminChildren('100000')
  const points = viewportSamples({width: 1000, height: 600, unproject: (x,y) => x<500 ? [120.15,30.27] : null})
  const fraction = points.filter(p => p && regions.some(r => containsRegion(r,p))).length / points.length
  assert.equal(fraction, .5)
  assert.equal(shouldExpand(.34,false), false)
  assert.equal(shouldExpand(.35,false), true)
  assert.equal(shouldExpand(.21,true), true)
  assert.equal(shouldExpand(.19,true), false)
})

test('real Equal Earth view expands China at 3x instead of waiting beyond 5x', async () => {
  const regions = await loadAdminChildren('100000')
  const projection = geoEqualEarth().fitExtent([[24,76],[1176,955]], {type:'Sphere'})
  const center = projection([105,35])!
  const coverageAt = (zoom: number) => {
    const samples = viewportSamples({width:1200,height:1000,unproject:(x,y)=>projection.invert!([(x-600)/zoom+center[0],(y-500)/zoom+center[1]])})
    return screenCoverage(samples, point=>regions.some(region=>containsRegion(region,point)))
  }
  assert.equal(shouldExpand(coverageAt(1),false),false)
  assert.equal(shouldExpand(coverageAt(3),false),true)
  assert.equal(shouldExpand(coverageAt(5),false),true)
  assert.equal(shouldExpand(coverageAt(2),true),true)
  assert.equal(shouldExpand(coverageAt(1),true),false)
})

test('normalization excludes decorative features and handles direct-admin districts as leaves', () => {
  const data: FeatureCollection = {type:'FeatureCollection', features:[{
    type:'Feature', properties:{adcode:110101,name:'东城区',level:'district',parent:{adcode:110000},childrenNum:8,center:[116.4,39.9]},
    geometry:{type:'Polygon', coordinates:[[[116,39],[117,39],[117,40],[116,40],[116,39]]]},
  }]}
  const child = normalizeRegions(data)[0]!
  assert.equal(child.properties.parent, '110000')
  assert.equal(child.properties.level, 3)
  assert.equal(child.properties.children, false)
  assert.ok(Math.abs(toWgs84([116.4,39.9])[0]-116.4)<.01)
})

test('failed downloads can retry and successful datasets are cached', async () => {
  const original = globalThis.fetch
  let calls = 0
  globalThis.fetch = async () => {
    calls++
    if (calls===1) return new Response('',{status:503})
    return Response.json({type:'FeatureCollection',features:[]})
  }
  try {
    await assert.rejects(loadAdminChildren('999999'))
    assert.deepEqual(await loadAdminChildren('999999'),[])
    await loadAdminChildren('999999')
    assert.equal(calls,2)
    await assert.rejects(loadAdminChildren('../invalid'))
  } finally { globalThis.fetch=original }
})


test('viewport culling retains small visible regions and conservatively handles the dateline', async () => {
  const {visibleRegions,loadAdminChildren}=await import('./adminRegions')
  const provinces=await loadAdminChildren('100000')
  const local=visibleRegions(provinces,[[120,30],[120.5,30.5]])
  assert.ok(local.some(region=>region.properties.code==='330000'))
  assert.ok(!local.some(region=>region.properties.code==='650000'))
  assert.ok(local.length<provinces.length/2)
  assert.equal(visibleRegions(provinces,[null]).length,0)
  assert.equal(visibleRegions(provinces,[[179,30],[-179,31]]).length,provinces.length)
})


test('multi-province overview retracts city detail while a province filling the view expands', async () => {
  const {shouldExpandSubdivision}=await import('./adminRegions')
  const regions=await loadAdminChildren('100000')
  // Broad central/western China view, similar to the reported multi-province screenshot.
  const overview=viewportSamples({width:800,height:1000,unproject:(x,y)=>[95+x/800*20,41-y/1000*16]})
  assert.ok(regions.every(region=>!shouldExpandSubdivision(overview,region,false)))
  assert.ok(regions.every(region=>!shouldExpandSubdivision(overview,region,true)))
  const zhejiang=regions.find(region=>region.properties.code==='330000')!
  const samples=(count:number)=>Array.from({length:400},(_,i)=>i<count ? [120.15,30.27] as [number,number] : null)
  assert.equal(shouldExpandSubdivision(samples(239),zhejiang,false),false)
  assert.equal(shouldExpandSubdivision(samples(240),zhejiang,false),true)
  assert.equal(shouldExpandSubdivision(samples(208),zhejiang,true),true)
  assert.equal(shouldExpandSubdivision(samples(207),zhejiang,true),false)
})
