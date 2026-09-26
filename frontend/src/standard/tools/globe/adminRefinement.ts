import { containsRegion, visibleRegions, loadAdminChildren, screenCoverage, shouldExpand, shouldExpandSubdivision, type AdminFeature } from './adminRegions'

export interface RefinementResult { features: AdminFeature[]; expanded: string[]; error: string; final: boolean }
/** Pick exactly one child branch. Occupancy decides focus; previous focus breaks ties.
 * This invariant holds even for overlapping source geometry or future threshold changes. */
export function focusedBranch(regions: AdminFeature[], samples: Array<[number,number] | null>, expanded: Set<string>): AdminFeature | undefined {
  return regions.filter(region=>region.properties.children && shouldExpandSubdivision(samples,region,expanded.has(region.properties.code)))
    .map(region=>({region,score:samples.filter(point=>point && containsRegion(region,point)).length}))
    .sort((a,b)=>b.score-a.score || Number(expanded.has(b.region.properties.code))-Number(expanded.has(a.region.properties.code)) || a.region.properties.code.localeCompare(b.region.properties.code))[0]?.region
}

/** A single root-to-leaf focus path: siblings remain visible but never recurse together. */
export async function refineAdminRegions(samples: Array<[number,number] | null>, expanded: Set<string>, cancelled: () => boolean, publish: (result:RefinementResult) => void, loadChildren = loadAdminChildren) {
  const next: AdminFeature[] = [], nextExpanded = new Set<string>()
  let failed = false
  const report = (final=false) => { if (!cancelled()) publish({features:[...next],expanded:[...nextExpanded],error:failed?'部分行政区边界加载失败':'',final}) }
  try {
    const provinces = await loadChildren('100000')
    if (cancelled()) return
    if (shouldExpand(screenCoverage(samples,p=>provinces.some(f=>containsRegion(f,p))),expanded.has('100000'))) {
      nextExpanded.add('100000')
      const visit = async (regions:AdminFeature[]) => {
        const visible=visibleRegions(regions,samples)
        next.push(...visible); report()
        // Yield before deeper work so a new viewport can cancel the old focus path.
        await new Promise(resolve=>setTimeout(resolve,0))
        if (cancelled()) return
        const focus=focusedBranch(visible,samples,expanded)
        if (!focus) return
        try {
          const children=await loadChildren(focus.properties.code)
          if (cancelled()) return
          if (children.length) {
            nextExpanded.add(focus.properties.code)
            await visit(children)
          }
        } catch { failed=true }
      }
      await visit(provinces)
    }
    report(true)
  } catch { if (!cancelled()) publish({features:next,expanded:[...nextExpanded],error:'行政区边界加载失败',final:true}) }
}
