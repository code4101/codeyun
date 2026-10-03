import type { Project } from '@/core/Project';
import { Settings } from '@/core/service/Settings';
import { MouseLocation } from '@/core/service/controlService/MouseLocation';
import { installTouchGestures, type TouchPoint } from './touchGestures';

/** Provider-side adapter: recognized gestures use the same public pointer and
 * document commands as desktop, including native undo and collaboration leases.
 * The host never reaches into graph controllers or synthesizes mouse buttons.
 */
export function installTouchInteraction(project: Project, options: {
  readOnly: () => boolean;
  changed: () => void;
  edit: (ids: string[], edit: () => void) => Promise<void>;
  error: (error: unknown) => void;
}) {
  const canvas = project.canvas.element;
  let tool: 'select' | 'multi' | 'box' | 'connect' | 'draw' = 'select';
  let lastPoint: TouchPoint | undefined, nativeDown: TouchPoint | undefined;
  let boxing = false, busy = false, connectingIds: string[] = [];
  const toolbar = document.createElement('div');
  toolbar.className = 'codeyun-touch-tools'; toolbar.setAttribute('role', 'toolbar');
  toolbar.setAttribute('aria-label', '触摸画布工具');
  toolbar.hidden = !matchMedia('(any-pointer: coarse)').matches;
  document.documentElement.classList.toggle('codeyun-touch-active', !toolbar.hidden);
  const buttons = new Map<string, HTMLButtonElement>();
  const world = (p: TouchPoint) => project.renderer.transformView2World(project.canvas.clientToView(p.x, p.y));
  const hit = (p: TouchPoint) => project.controllerUtils.getClickedStageObject(world(p));
  const refresh = () => {
    for (const [id, button] of buttons) button.setAttribute('aria-pressed', String(id === tool));
    toolbar.dataset.connecting = String(tool === 'connect' && connectingIds.length > 0);
    toolbar.setAttribute('aria-label', tool === 'connect' ? (connectingIds.length ? '点击目标节点连线，Esc 退出' : '点击起点节点连线，Esc 退出') : '触摸画布工具');
    project.controller.resetCountdownTimer(); project.renderer.tick();
  };
  const cursor = (p: TouchPoint) => {
    lastPoint = p;
    const view = project.canvas.clientToView(p.x, p.y);
    MouseLocation.x = view.x; MouseLocation.y = view.y; MouseLocation.clientX = p.x; MouseLocation.clientY = p.y;
  };
  const forward = (type: string, p: TouchPoint, detail = 0) => {
    cursor(p);
    // A provider-defined pointer type survives the collaboration replay queue.
    // ControllerClass only permits detail=2 to invoke touch double-click actions.
    canvas.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true,
      pointerType: 'codeyun-touch', pointerId: 2147483000, isPrimary: true,
      clientX: p.x, clientY: p.y, button: 0, buttons: type === 'pointerup' ? 0 : 1,
      ctrlKey: p.ctrlKey, metaKey: p.metaKey, shiftKey: p.shiftKey, altKey: p.altKey, detail }));
  };
  const select = (p: TouchPoint, double = false) => {
    forward('pointerdown', p); forward('pointerup', p, double ? 2 : 0);
  };
  const menu = (p: TouchPoint) => {
    const object = hit(p);
    if (object && !object.isSelected) { project.stageManager.clearSelectAll(); object.isSelected = true; }
    cursor(p); refresh();
    canvas.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, button: 2, clientX: p.x, clientY: p.y }));
  };
  const setTool = (next: typeof tool) => {
    gestures.reset(); tool = next;
    Settings.mouseLeftMode = next === 'draw' ? 'draw' : 'selectAndMove';
    connectingIds = next === 'connect' ? project.stageManager.getConnectableEntity().filter(item => item.isSelected).map(item => item.uuid) : [];
    canvas.focus({ preventScroll: true }); refresh();
  };
  const gestures = installTouchGestures(canvas, {
    focus(p) {
      toolbar.hidden = false; document.documentElement.classList.add('codeyun-touch-active'); cursor(p); canvas.focus({ preventScroll: true });
      // Stored desktop drawing mode is never an implicit finger drawing tool.
      Settings.mouseLeftMode = tool === 'draw' ? 'draw' : 'selectAndMove'; refresh();
    },
    target(p) { return hit(p)?.uuid ?? null; },
    tap(p, double) {
      if (busy) return;
      cursor(p);
      if (tool === 'connect' && !options.readOnly()) {
        const target = project.stageManager.findConnectableEntityByLocation(world(p));
        if (!target) return; // Blank taps never create a node as a side effect of linking.
        if (!connectingIds.length) {
          connectingIds = [target.uuid]; project.stageManager.clearSelectAll(); target.isSelected = true; refresh(); return;
        }
        if (connectingIds.includes(target.uuid)) return;
        const sources = [...connectingIds], destination = target.uuid;
        busy = true;
        void options.edit([...sources, destination], () => {
          const nodes = project.stageManager.getConnectableEntity();
          const end = nodes.find(item => item.uuid === destination);
          const from = nodes.filter(item => sources.includes(item.uuid));
          if (!end || !from.length || options.readOnly()) return;
          project.historyManager.recordStep();
          project.stageManager.connectMultipleEntities(from, end);
          project.historyManager.recordStep(); options.changed();
        }).catch(options.error).finally(() => { busy = false; refresh(); });
      } else if (tool === 'multi') {
        const object = hit(p); if (object) object.isSelected = !object.isSelected;
      } else if (tool !== 'draw') {
        select(p, double && !options.readOnly());
      }
      refresh();
    },
    hold: menu,
    start(p) {
      if (busy || tool === 'connect' || project.controller.isCameraLocked || project.controller.pressingKeySet.has(' ')) return 'pan';
      if (tool === 'box') { boxing = true; project.rectangleSelect.startSelecting(world(p)); return 'edit'; }
      if ((hit(p) || project.controllerUtils.isClickedResizeRect(world(p)) || tool === 'draw') && !options.readOnly()) {
        nativeDown = p; forward('pointerdown', p); return 'edit';
      }
      return 'pan';
    },
    move(p) {
      cursor(p);
      if (boxing) project.rectangleSelect.moveSelecting(world(p));
      else if (nativeDown) forward('pointermove', p);
      refresh();
    },
    end(p, cancelled) {
      if (boxing) {
        if (cancelled) project.rectangleSelect.shutDown(); else project.rectangleSelect.endSelecting();
        boxing = false; tool = 'select';
      }
      if (nativeDown) {
        // Cancellation uses its own public signal so a pending lease replay
        // cannot start an edit after the fingers have switched to navigation.
        if (cancelled) {
          canvas.dispatchEvent(new PointerEvent('pointercancel', { bubbles: true, pointerType: 'codeyun-touch', pointerId: 2147483000 }));
          canvas.dispatchEvent(new PointerEvent('pointerleave', { pointerType: 'codeyun-touch', clientX: p.x, clientY: p.y }));
        }
        forward('pointerup', p); nativeDown = undefined;
        project.historyManager.recordStep(); options.changed();
      }
      refresh();
    },
    navigate(previous, current, ratio) {
      if (project.controller.isCameraLocked) return;
      project.camera.stopImmediately();
      const anchor = world(previous);
      const scale = Math.min(100, Math.max(0.02, project.camera.currentScale * ratio));
      project.camera.currentScale = project.camera.targetScale = scale;
      const shifted = world(current);
      project.camera.location = project.camera.location.add(anchor.subtract(shifted));
      project.camera.targetLocationByScale = anchor;
      cursor(current); refresh();
    },
  });
  for (const [id, label] of [['select', '选择'], ['multi', '多选'], ['box', '框选'], ['connect', '连线'], ['draw', '绘画']] as const) {
    if (options.readOnly() && (id === 'connect' || id === 'draw')) continue;
    const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
    button.dataset.touchTool = id; button.onclick = () => setTool(tool === id ? 'select' : id);
    toolbar.append(button); buttons.set(id, button);
  }
  const more = document.createElement('button'); more.type = 'button'; more.textContent = '更多';
  more.onclick = () => {
    const rect = canvas.getBoundingClientRect();
    menu(lastPoint ?? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
  };
  toolbar.append(more); document.body.append(toolbar); refresh();
  const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setTool('select'); };
  window.addEventListener('keydown', escape);
  return { setMode(mode: string) { setTool(mode === 'draw' ? 'draw' : mode === 'connectAndCut' ? 'connect' : 'select'); },
    dispose() { gestures.dispose(); toolbar.remove(); document.documentElement.classList.remove('codeyun-touch-active'); window.removeEventListener('keydown', escape); } };
}
