export type PageCrop = { left: number; top: number; right: number; bottom: number }
export const FULL_PAGE: PageCrop = { left: 0, top: 0, right: 0, bottom: 0 }

/** Work on unthemed pixels. Projection thresholds discard isolated scanner specks; padding protects ink. */
export function detectPageCrop(data: Uint8ClampedArray, width: number, height: number): PageCrop {
  const rows = new Uint32Array(height)
  const cols = new Uint32Array(width)
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const i = (y * width + x) * 4
    if (data[i + 3]! > 128 && Math.min(data[i]!, data[i + 1]!, data[i + 2]!) < 210) { rows[y]++; cols[x]++ }
  }
  const minRow = Math.max(3, Math.ceil(width * .01))
  const minCol = Math.max(3, Math.ceil(height * .005))
  const bounds = (counts: Uint32Array, threshold: number) => {
    let first = -1, last = -1, run = 0
    for (let index = 0; index < counts.length; index++) {
      run = counts[index]! >= threshold ? run + 1 : 0
      if (run >= 3) { if (first < 0) first = index - run + 1; last = index }
    }
    return [first, last] as const
  }
  // A one-pixel scanner border must not pin the crop to the edge of the sheet.
  const [top, bottom] = bounds(rows, minRow)
  const [left, right] = bounds(cols, minCol)
  if (top < 0 || left < 0) return { ...FULL_PAGE }
  const padding = Math.max(6, Math.ceil(Math.min(width, height) * .012))
  return {
    left: Math.max(0, Math.min(.35, (left - padding) / width)),
    top: Math.max(0, Math.min(.35, (top - padding) / height)),
    right: Math.max(0, Math.min(.35, (width - 1 - right - padding) / width)),
    bottom: Math.max(0, Math.min(.35, (height - 1 - bottom - padding) / height)),
  }
}
