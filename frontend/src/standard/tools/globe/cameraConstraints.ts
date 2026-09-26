/**
 * Navigation and orientation are independent: preserve the engine's center/zoom,
 * always face the surface directly and lock north-up. MapLibre handles
 * its own latitude bounds (~85.05°) and latitude-dependent globe scale.
 * Do not clamp latitude here: that prevents high-zoom exploration of polar regions.
 */
export function constrainGlobeCamera(latitude: number, zoom: number) {
  return { latitude, zoom, pitch: 0, bearing: 0, roll: 0 }
}
