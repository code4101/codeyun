export const GLOBE_CENTER_LATITUDE = 25

/** Keep navigation within the polar boundary while locking the camera upright. */
export function constrainGlobeCamera(latitude: number, zoom: number) {
  return { latitude: Math.max(-85, Math.min(85, latitude)), zoom, pitch: 0, bearing: 0, roll: 0 }
}

export type DragAxis = 'horizontal' | 'vertical'
/** Undo the engine's scale compensation when axis locking changes its proposed latitude. */
export function preserveGlobeScale(zoom: number, proposedLatitude: number, actualLatitude: number) {
  const cosine = (latitude: number) => Math.cos(latitude * Math.PI / 180)
  return zoom + Math.log2(cosine(actualLatitude) / cosine(proposedLatitude))
}

/** Ignore click jitter; choose the dominant screen axis once per drag. */
export function chooseDragAxis(dx: number, dy: number): DragAxis | null {
  if (Math.hypot(dx, dy) < 5) return null
  return Math.abs(dx) >= Math.abs(dy) ? 'horizontal' : 'vertical'
}
