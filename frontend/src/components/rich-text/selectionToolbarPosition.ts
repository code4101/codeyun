export interface SelectionRect { left: number; top: number; right: number; bottom: number }

/** Viewport coordinates only: callers teleport the fixed toolbar to body. */
export function selectionToolbarPosition(
  rects: SelectionRect[], viewport: SelectionRect,
  size: { width: number; height: number }, backwards = false,
) {
  const gap = 8
  const visible = rects.map(rect => ({
    left: Math.max(rect.left, viewport.left), top: Math.max(rect.top, viewport.top),
    right: Math.min(rect.right, viewport.right), bottom: Math.min(rect.bottom, viewport.bottom),
  })).filter(rect => rect.right > rect.left && rect.bottom > rect.top)
  const anchor = backwards ? visible[0] : visible[visible.length - 1]
  if (!anchor) return null
  const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(value, Math.max(min, max)))
  // Use a visible line at the selection's focus end, not the union of multiple
  // lines/columns (whose centre can fall in an entirely different page).
  const above = anchor.top - size.height - gap
  const top = above >= viewport.top + gap ? above : anchor.bottom + gap
  return {
    left: clamp((anchor.left + anchor.right - size.width) / 2, viewport.left + gap, viewport.right - size.width - gap),
    top: clamp(top, viewport.top + gap, viewport.bottom - size.height - gap),
  }
}
