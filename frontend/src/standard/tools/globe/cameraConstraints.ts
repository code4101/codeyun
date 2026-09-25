export const MAX_GLOBE_ANGLE = 30

/** Keep the north-up globe away from pole crossings without changing its apparent radius. */
export function constrainGlobeCamera(latitude: number, zoom: number, pitch: number) {
  const constrainedPitch = Math.max(0, Math.min(MAX_GLOBE_ANGLE, pitch))
  const latitudeLimit = MAX_GLOBE_ANGLE
  const constrainedLatitude = Math.max(-latitudeLimit, Math.min(latitudeLimit, latitude))
  // MapLibre's globe radius is proportional to 2^zoom / cos(latitude).
  // When clamping latitude, compensate zoom so this ratio stays constant.
  const cosine = (degrees: number) => Math.cos(degrees * Math.PI / 180)
  const constrainedZoom = zoom + Math.log2(cosine(constrainedLatitude) / cosine(latitude))
  return { latitude: constrainedLatitude, zoom: constrainedZoom, pitch: constrainedPitch }
}
