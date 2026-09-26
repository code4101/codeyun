import { refineAdminRegions } from './adminRefinement'
let current = 0
self.onmessage = (event: MessageEvent<{id:number; samples?: Array<[number,number] | null>; expanded?:string[]}>) => {
  const {id,samples,expanded}=event.data
  current=id
  if (samples) void refineAdminRegions(samples,new Set(expanded),()=>current!==id,result=>self.postMessage({id,...result}))
}
