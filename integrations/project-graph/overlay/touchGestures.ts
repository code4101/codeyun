export type TouchPoint = { x: number; y: number; ctrlKey?: boolean; metaKey?: boolean; shiftKey?: boolean; altKey?: boolean };
export type TouchActions = {
  focus(point: TouchPoint): void;
  target(point: TouchPoint): string | null;
  tap(point: TouchPoint, double: boolean): void;
  hold(point: TouchPoint): void;
  start(point: TouchPoint): 'edit' | 'pan';
  move(point: TouchPoint): void;
  end(point: TouchPoint, cancelled: boolean): void;
  navigate(previous: TouchPoint, current: TouchPoint, scale: number): void;
};

/** One owner for finger input. Native TouchEvents must never also reach the
 * mouse controllers: their release-based double-click detector treats swipes
 * and duplicate pointer/touch releases as clicks. Mouse and Pencil pass through.
 */
export function installTouchGestures(canvas: HTMLCanvasElement, actions: TouchActions) {
  const win = canvas.ownerDocument.defaultView!;
  const fingers = new Map<number, TouchPoint>();
  let phase: 'pending' | 'edit' | 'pan' | 'held' | 'multi' | 'suspended' = 'pending';
  let start: TouchPoint = { x: 0, y: 0 }, last = start;
  let startedAt = 0, timer: ReturnType<typeof setTimeout> | undefined;
  let pair: { center: TouchPoint; distance: number } | undefined;
  let previousTap: { point: TouchPoint; target: string | null; time: number } | undefined;
  const distance = (a: TouchPoint, b: TouchPoint) => Math.hypot(a.x - b.x, a.y - b.y);
  const point = (e: PointerEvent): TouchPoint => ({ x: e.clientX, y: e.clientY, ctrlKey: e.ctrlKey, metaKey: e.metaKey, shiftKey: e.shiftKey, altKey: e.altKey });
  const clearHold = () => { clearTimeout(timer); timer = undefined; };
  const block = (event: Event) => { event.preventDefault(); event.stopImmediatePropagation(); };
  const getPair = () => {
    const [a, b] = [...fingers.values()];
    return { center: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, distance: distance(a, b) };
  };
  const endEdit = (cancelled: boolean) => { if (phase === 'edit') actions.end(last, cancelled); };
  const down = (event: PointerEvent) => {
    if (event.pointerType !== 'touch') return;
    block(event);
    const p = point(event);
    fingers.set(event.pointerId, p);
    try { canvas.setPointerCapture(event.pointerId); } catch { /* Detached canvas. */ }
    clearHold();
    if (fingers.size > 1) {
      endEdit(true); phase = 'multi'; previousTap = undefined; pair = getPair(); return;
    }
    start = last = p; startedAt = Date.now(); phase = 'pending';
    actions.focus(p);
    timer = setTimeout(() => {
      if (phase !== 'pending' || fingers.size !== 1) return;
      phase = 'held'; previousTap = undefined; actions.hold(start);
    }, 500);
  };
  const move = (event: PointerEvent) => {
    if (event.pointerType !== 'touch' || !fingers.has(event.pointerId)) return;
    block(event);
    const p = point(event); fingers.set(event.pointerId, p);
    if (phase === 'multi') {
      const next = getPair();
      if (pair) actions.navigate(pair.center, next.center, pair.distance > 0 ? next.distance / pair.distance : 1);
      pair = next; return;
    }
    if (phase === 'held' || phase === 'suspended') return;
    if (phase === 'pending') {
      if (distance(start, p) <= 8) return;
      clearHold(); previousTap = undefined; phase = actions.start(start);
    }
    if (phase === 'edit') actions.move(p);
    else if (phase === 'pan') actions.navigate(last, p, 1);
    last = p;
  };
  const up = (event: PointerEvent) => {
    if (event.pointerType !== 'touch' || !fingers.has(event.pointerId)) return;
    block(event); clearHold();
    const p = point(event);
    const cancelled = event.type !== 'pointerup';
    if (phase === 'edit') actions.end(cancelled ? last : p, cancelled);
    if (!cancelled && phase === 'pending' && distance(start, p) <= 8 && Date.now() - startedAt < 500) {
      const target = actions.target(p), now = Date.now();
      const double = !!previousTap && now - previousTap.time <= 350 && previousTap.target === target && distance(previousTap.point, p) <= 20;
      previousTap = double ? undefined : { point: p, target, time: now };
      actions.tap(p, double);
    } else previousTap = undefined;
    fingers.delete(event.pointerId);
    if (fingers.size >= 2) { phase = 'multi'; pair = getPair(); }
    else if (fingers.size) { phase = 'suspended'; pair = undefined; }
    else { phase = 'pending'; pair = undefined; }
    if (canvas.hasPointerCapture?.(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
  };
  const reset = () => {
    clearHold(); endEdit(true);
    const ids = [...fingers.keys()]; fingers.clear(); phase = 'pending'; pair = undefined; previousTap = undefined;
    for (const id of ids) if (canvas.hasPointerCapture?.(id)) canvas.releasePointerCapture(id);
  };
  const key = (event: KeyboardEvent) => { if (event.key === 'Escape') reset(); };
  const visibility = () => { if (canvas.ownerDocument.hidden) reset(); };
  const originalAction = canvas.style.touchAction;
  canvas.style.touchAction = 'none';
  canvas.addEventListener('pointerdown', down, true);
  win.addEventListener('pointermove', move, true);
  win.addEventListener('pointerup', up, true);
  win.addEventListener('pointercancel', up, true);
  canvas.addEventListener('lostpointercapture', up, true);
  const legacy = ['touchstart', 'touchmove', 'touchend', 'touchcancel'] as const;
  legacy.forEach(type => canvas.addEventListener(type, block, { capture: true, passive: false }));
  win.addEventListener('blur', reset); win.addEventListener('keydown', key, true);
  canvas.ownerDocument.addEventListener('visibilitychange', visibility);
  return { reset, dispose() {
    reset(); canvas.style.touchAction = originalAction;
    canvas.removeEventListener('pointerdown', down, true);
    win.removeEventListener('pointermove', move, true); win.removeEventListener('pointerup', up, true);
    win.removeEventListener('pointercancel', up, true); canvas.removeEventListener('lostpointercapture', up, true);
    legacy.forEach(type => canvas.removeEventListener(type, block, true));
    win.removeEventListener('blur', reset); win.removeEventListener('keydown', key, true);
    canvas.ownerDocument.removeEventListener('visibilitychange', visibility);
  } };
}
