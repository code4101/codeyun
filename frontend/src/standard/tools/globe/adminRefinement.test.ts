import { test } from 'node:test'
import assert from 'node:assert/strict'
import { refineAdminRegions, type RefinementResult } from './adminRefinement'
import type { AdminFeature } from './adminRegions'

function region(code:string,parent:string,level:number,west:number,east:number):AdminFeature {
  return {type:'Feature',properties:{code,parent,level,name:code,children:level<3,label:[(west+east)/2,0]},
    geometry:{type:'Polygon',coordinates:[[[west,-2],[east,-2],[east,2],[west,2],[west,-2]]]}}
}

test('only one root-to-leaf branch expands, including overlapping geometry; switching focus removes the old descendants', async()=>{
  const a=region('110000','100000',1,-2,1), b=region('220000','100000',1,0,2)
  const tree:Record<string,AdminFeature[]>={
    '100000':[a,b],
    '110000':[region('110100','110000',2,-2,1),region('110200','110000',2,-2,1)],
    '220000':[region('220100','220000',2,0,2),region('220200','220000',2,0,2)],
    '110100':[region('110101','110100',3,-2,1)],
    '220100':[region('220101','220100',3,0,2)],
  }
  const run=async(lng:number,previous:string[])=>{
    const requests:string[]=[], snapshots:RefinementResult[]=[]
    await refineAdminRegions(Array.from({length:400},()=>[lng,0] as [number,number]),new Set(previous),()=>false,
      result=>snapshots.push(result),async code=>{requests.push(code);return tree[code]??[]})
    return {requests,snapshots,final:snapshots[snapshots.length-1]!}
  }
  const first=await run(.5,['100000','220000'])
  assert.deepEqual(first.requests,['100000','220000','220100'])
  assert.deepEqual(first.final.expanded,['100000','220000','220100'])
  assert.ok(first.final.features.some(f=>f.properties.code==='110000'))
  assert.ok(first.final.features.some(f=>f.properties.code==='220200'))
  const second=await run(-1,first.final.expanded)
  assert.deepEqual(second.requests,['100000','110000','110100'])
  assert.deepEqual(second.final.expanded,['100000','110000','110100'])
  assert.ok(second.snapshots.every(snapshot=>snapshot.features.every(f=>!['220100','220200','220101'].includes(f.properties.code))))
})

