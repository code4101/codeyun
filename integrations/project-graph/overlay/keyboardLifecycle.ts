/** Browser focus can end a key press without delivering keyup to the canvas.
 * Repair held-state through the controller's public API and repaint immediately;
 * never synthesize shortcuts (keyup can itself edit a graph).
 */
export function installKeyboardLifecycle(project: {
  controller: { pressingKeySet: Set<string>; resetCountdownTimer(): void };
  renderer: { tick(): void };
  loop(): void;
}, win: Window = window, doc: Document = document) {
  const controller = project.controller;
  const repaint = () => { controller.resetCountdownTimer(); project.renderer.tick(); };
  const clear = () => { controller.pressingKeySet.clear(); repaint(); };
  const editable = () => doc.activeElement?.closest('input, textarea, [contenteditable]:not([contenteditable="false"])');
  const reconcile = (event: MouseEvent | KeyboardEvent) => {
    let changed = false;
    for (const [key, held] of [['alt', event.altKey], ['control', event.ctrlKey], ['shift', event.shiftKey], ['meta', event.metaKey]] as const) {
      if (!held) changed = controller.pressingKeySet.delete(key) || changed;
    }
    if (changed) repaint();
  };
  const down = (event: KeyboardEvent) => {
    reconcile(event);
    if (editable()) return;
    project.loop();
    // Alt belongs to graph shortcuts while the canvas has focus, not browser menus.
    if (event.key === 'Alt') event.preventDefault();
  };
  const up = (event: KeyboardEvent) => {
    controller.pressingKeySet.delete(event.key.toLowerCase());
    reconcile(event);
    repaint();
  };
  const visibility = () => { if (doc.hidden) clear(); };
  const focus = () => { if (editable()) clear(); };
  win.addEventListener('keydown', down, { capture: true });
  win.addEventListener('keyup', up, { capture: true });
  win.addEventListener('pointermove', reconcile, { capture: true });
  win.addEventListener('pointerdown', reconcile, { capture: true });
  win.addEventListener('blur', clear);
  doc.addEventListener('visibilitychange', visibility);
  doc.addEventListener('focusin', focus);
  return () => {
    win.removeEventListener('keydown', down, { capture: true });
    win.removeEventListener('keyup', up, { capture: true });
    win.removeEventListener('pointermove', reconcile, { capture: true });
    win.removeEventListener('pointerdown', reconcile, { capture: true });
    win.removeEventListener('blur', clear);
    doc.removeEventListener('visibilitychange', visibility);
    doc.removeEventListener('focusin', focus);
  };
}
