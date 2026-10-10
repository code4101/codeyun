/** Desktop macOS in a browser. iPadOS Safari reports a Macintosh UA but has touch points. */
export function detectDesktopMac(userAgent: string, maxTouchPoints: number) {
  return userAgent.includes('Macintosh') && maxTouchPoints <= 1;
}
export const isDesktopMacBrowser = typeof navigator !== 'undefined' && detectDesktopMac(navigator.userAgent, navigator.maxTouchPoints ?? 0);

type CameraWheel = { ctrlKey: boolean; deltaX: number; deltaMode?: number; wheelDeltaY?: number };
/**
 * Browsers report a trackpad pinch as a wheel event with ctrlKey and no Control keydown.
 * Other events cannot be attributed reliably to a device
 * (https://developer.mozilla.org/en-US/docs/Web/API/Element/wheel_event), so the user may choose.
 * 'auto' is only a compatibility heuristic: line/page deltaMode is a mouse; otherwise
 * legacy wheelDeltaY in whole 120s (https://developer.mozilla.org/docs/Web/API/Element/mousewheel_event)
 * counts as a notch, which can misjudge trackpads and smooth mice.
 */
export function classifyMacWheel(event: CameraWheel, controlKeyDown: boolean, device: 'auto' | 'trackpad' | 'mouse' = 'auto'): 'pinch' | 'wheel' | 'pan' {
  if (event.ctrlKey && !controlKeyDown) return 'pinch';
  if (device !== 'auto') return device === 'mouse' ? 'wheel' : 'pan';
  if (event.deltaMode) return 'wheel';
  const ticks = event.wheelDeltaY ?? 0;
  return event.deltaX === 0 && ticks !== 0 && ticks % 120 === 0 ? 'wheel' : 'pan';
}
