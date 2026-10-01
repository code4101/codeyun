import { Project } from '@/core/Project';
import { Settings } from '@/core/service/Settings';
import { Entity } from '@/core/stage/stageObject/abstract/StageEntity';
import type { GalleryCanvasDrag } from '../../../frontend/src/plugins/modules/project-graph/gallery.ts';

/** Keep PG's normal movement inside the canvas. Pointer capture lets the same
 * physical drag continue across its iframe; after leaving, only the host moves
 * the drag preview. Drop targets and storage commands remain host-owned. */
export function installGalleryCanvasDrag(project: Project, options: {
  enabled: () => boolean; begin: () => void; publish: (value: GalleryCanvasDrag) => void;
}) {
  const canvas = project.canvas.element;
  let gesture: { pointerId: number; ids: string[]; title: string; outside: boolean; startX: number; startY: number } | undefined;
  const payload = (phase: GalleryCanvasDrag['phase'], x: number, y: number): GalleryCanvasDrag => ({
    phase, x, y, ids: gesture?.ids ?? [], title: gesture?.title ?? '选中子图',
  });
  const stopNative = (event: PointerEvent) => {
    canvas.dispatchEvent(new PointerEvent('pointerleave', { pointerId: event.pointerId, clientX: event.clientX, clientY: event.clientY }));
  };
  const finish = (event?: PointerEvent, cancelled = false) => {
    if (!gesture) return;
    if (gesture.outside) options.publish(payload(cancelled ? 'cancel' : 'end', event?.clientX ?? 0, event?.clientY ?? 0));
    const pointerId = gesture.pointerId; gesture = undefined;
    // Escape/blur releases capture before the physical mouse-up can reach this
    // iframe. Notify window gesture observers (including collaboration leases).
    if (cancelled) window.dispatchEvent(new PointerEvent('pointerup', { pointerId, button: 0, buttons: 0 }));
    if (canvas.hasPointerCapture(pointerId)) canvas.releasePointerCapture(pointerId);
  };
  const down = (event: PointerEvent) => {
    if (!options.enabled() || event.button !== 0 || Settings.mouseLeftMode !== 'selectAndMove' || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || project.controller.camera.isPreGrabbingWhenSpace) return;
    const point = project.renderer.transformView2World(project.canvas.clientToView(event.clientX, event.clientY));
    if (project.controllerUtils.isClickedResizeRect(point)) return;
    const clicked = project.controllerUtils.getClickedStageObject(point);
    if (!(clicked instanceof Entity) || project.sectionMethods.isObjectBeLockedBySection(clicked)) return;
    queueMicrotask(() => {
      if (!options.enabled() || !clicked.isSelected) return;
      const selected = project.stage.filter(item => item.isSelected);
      gesture = { pointerId: event.pointerId, ids: selected.map(item => item.uuid), title: 'text' in clicked ? String(clicked.text) : '选中子图', outside: false, startX: event.clientX, startY: event.clientY };
      try { canvas.setPointerCapture(event.pointerId); } catch { gesture = undefined; }
    });
  };
  const move = (event: PointerEvent) => {
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    if (!options.enabled() || !(event.buttons & 1)) { stopNative(event); finish(event, true); return; }
    const rect = canvas.getBoundingClientRect();
    const outside = event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
    if (outside && Math.hypot(event.clientX - gesture.startX, event.clientY - gesture.startY) > 5) {
      if (!gesture.outside) { gesture.outside = true; stopNative(event); options.begin(); }
    }
    if (gesture.outside) {
      event.stopImmediatePropagation(); event.preventDefault();
      options.publish(payload('move', event.clientX, event.clientY));
    }
  };
  const up = (event: PointerEvent) => {
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    if (gesture.outside) { event.stopImmediatePropagation(); event.preventDefault(); }
    finish(event);
  };
  const cancel = (event: PointerEvent) => { if (gesture?.pointerId === event.pointerId) { stopNative(event); finish(event, true); } };
  const escape = (event: KeyboardEvent) => {
    if (gesture && event.key === 'Escape') { canvas.dispatchEvent(new PointerEvent('pointerleave')); finish(undefined, true); }
  };
  const blur = () => {
    if (gesture) { canvas.dispatchEvent(new PointerEvent('pointerleave')); finish(undefined, true); }
  };
  // Bubble down observes native selection; captured moves run before native movement.
  canvas.addEventListener('pointerdown', down);
  canvas.addEventListener('pointermove', move, true);
  canvas.addEventListener('pointerup', up, true);
  canvas.addEventListener('pointercancel', cancel);
  canvas.addEventListener('lostpointercapture', cancel);
  window.addEventListener('keydown', escape, true);
  window.addEventListener('blur', blur);
  return { dispose() {
    finish(undefined, true);
    canvas.removeEventListener('pointerdown', down); canvas.removeEventListener('pointermove', move, true);
    canvas.removeEventListener('pointerup', up, true); canvas.removeEventListener('pointercancel', cancel);
    canvas.removeEventListener('lostpointercapture', cancel); window.removeEventListener('keydown', escape, true);
    window.removeEventListener('blur', blur);
  } };
}
