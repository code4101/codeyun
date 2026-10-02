import { Project } from '@/core/Project';
import { Vector } from '@graphif/data-structures';
import { galleryObjects } from './galleryArchive';
import { GALLERY_ITEM, galleryBounds, galleryGeometry, type GalleryDrop } from '../../../frontend/src/plugins/modules/project-graph/gallery.ts';

/** Preview is disposable view state. Only a drop commits a positioned take.
 * Native client/view/world APIs account for canvas bounds, pan and zoom. */
export function installGalleryCanvasDrop(project: Project, options: {
  enabled: () => boolean; publish: (value: GalleryDrop) => void;
}) {
  const canvas = project.canvas.element;
  let source: { documentId: string; itemId: string } | null = null;
  const preview = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  preview.setAttribute('data-gallery-placement-preview', '');
  Object.assign(preview.style, { position: 'fixed', pointerEvents: 'none', zIndex: '10000', display: 'none', overflow: 'hidden' });
  document.body.append(preview);
  const hide = () => { preview.style.display = 'none'; };
  const inside = (event: DragEvent) => {
    const rect = canvas.getBoundingClientRect();
    return event.clientX >= rect.left && event.clientX < rect.right && event.clientY >= rect.top && event.clientY < rect.bottom;
  };
  const position = (event: DragEvent) => {
    const world = project.renderer.transformView2World(project.canvas.clientToView(event.clientX, event.clientY));
    return { x: world.x, y: world.y };
  };
  const objects = () => source && galleryObjects(project)[GALLERY_ITEM + source.itemId]?.objects;
  const accepts = (event: DragEvent) => options.enabled() && event.target === canvas && !!objects() && inside(event)
    && !!event.dataTransfer?.types.includes('application/x-codeyun-gallery-item');
  const over = (event: DragEvent) => {
    if (!accepts(event)) { hide(); return; }
    event.preventDefault(); event.dataTransfer!.dropEffect = 'move';
    const stored = objects()!, bounds = galleryBounds(stored), target = position(event);
    const dx = target.x - bounds.x - bounds.width / 2, dy = target.y - bounds.y - bounds.height / 2;
    const rect = canvas.getBoundingClientRect();
    Object.assign(preview.style, { display: 'block', left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px` });
    preview.replaceChildren();
    for (const node of galleryGeometry(stored).slice(0, 32)) {
      // Keep preview geometry in view space; CSS conversion uses native canvas API.
      const origin = project.canvas.viewToClient(project.renderer.transformWorld2View(new Vector(node.x + dx, node.y + dy)));
      const end = project.canvas.viewToClient(project.renderer.transformWorld2View(new Vector(node.x + dx + node.width, node.y + dy + node.height)));
      const shape = document.createElementNS(preview.namespaceURI, 'rect');
      for (const [key, value] of Object.entries({ x: origin.x - rect.left, y: origin.y - rect.top, width: Math.max(2, end.x - origin.x), height: Math.max(2, end.y - origin.y), rx: 4, fill: '#76a9ff33', stroke: '#76a9ff', 'stroke-width': 1.5 })) shape.setAttribute(key, String(value));
      preview.append(shape);
    }
  };
  const drop = (event: DragEvent) => {
    hide();
    if (!accepts(event)) return;
    event.preventDefault();
    try {
      const value = JSON.parse(event.dataTransfer!.getData('application/x-codeyun-gallery-item'));
      if (value.documentId === source!.documentId && value.itemId === source!.itemId)
        options.publish({ ...source!, position: position(event) });
    } catch { /* Ignore invalid or foreign drag payloads. */ }
    source = null;
  };
  const leave = (event: DragEvent) => { if (!inside(event)) hide(); };
  document.addEventListener('dragover', over);
  document.addEventListener('drop', drop);
  document.addEventListener('dragleave', leave);
  return {
    announce(value: typeof source) { source = value; hide(); },
    dispose() { preview.remove(); document.removeEventListener('dragover', over); document.removeEventListener('drop', drop); document.removeEventListener('dragleave', leave); },
  };
}
