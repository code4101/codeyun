/** Fit the full, untilted globe with a small margin, using MapLibre's 512px world scale.
 * Perspective silhouette radius r = fR / sqrt(f² + 2fR), where f is focal distance
 * and R = 512 * 2^zoom / (2π cos(latitude)). Invert it to avoid empirical zoom constants.
 */
export function fitGlobeZoom(width: number, height: number, latitude: number, verticalFov: number): number | null {
  if (width <= 0 || height <= 0) return null
  const radius = Math.min(width, height) * 0.46
  const focal = height / (2 * Math.tan(verticalFov * Math.PI / 360))
  const globeRadius = (radius * radius + radius * Math.hypot(radius, focal)) / focal
  return Math.log2(globeRadius * 2 * Math.PI * Math.cos(latitude * Math.PI / 180) / 512)
}
