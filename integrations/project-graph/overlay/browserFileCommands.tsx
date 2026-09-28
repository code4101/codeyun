import { Project } from '@/core/Project';
import { createImageNodeFromBlob } from '@/core/service/dataManageService/imageNodeFactory';
import { prepareImageBlobForImport } from '@/core/service/dataManageService/imageUtils';
import { StageExportSvg, type SvgExportConfig } from '@/core/service/dataGenerateService/stageExportEngine/StageExportSvg';
import { ImageNode } from '@/core/stage/stageObject/entity/ImageNode';
import { SvgNode } from '@/core/stage/stageObject/entity/SvgNode';
import { TextNode } from '@/core/stage/stageObject/entity/TextNode';
import { CollisionBox } from '@/core/stage/stageObject/collisionBox/collisionBox';
import { Rectangle } from '@graphif/shapes';
import { Vector } from '@graphif/data-structures';

/** User-selected files only; browser commands never manufacture desktop paths. */
export function pickFiles(accept: string, multiple = true): Promise<File[]> {
  return new Promise(resolve => {
    const input = document.createElement('input');
    input.type = 'file'; input.accept = accept; input.multiple = multiple;
    input.hidden = true; document.body.append(input);
    const finish = (files: File[]) => { input.remove(); resolve(files); };
    input.onchange = () => finish(Array.from(input.files ?? []));
    input.oncancel = () => finish([]);
    input.click();
  });
}
export function downloadBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url; link.download = name.replace(/[\\/:*?"<>|]/g, '_'); link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

export async function importCanvasFiles(project: Project, kind: 'image' | 'svg' | 'text') {
  const files = await pickFiles(kind === 'image' ? '.png,.jpg,.jpeg,.webp,.gif' : kind === 'svg' ? '.svg' : '.txt,.md,.markdown,.csv,.json,.log');
  let changed = false;
  try {
    for (const [index, file] of files.entries()) {
      const location = project.camera.location.clone().add(new Vector(index * 50, index * 50));
      if (kind === 'image') {
        const prepared = await prepareImageBlobForImport(file);
        await createImageNodeFromBlob(project, prepared.blob, { location, intrinsicSize: prepared });
      } else if (kind === 'svg') {
        const svg = new DOMParser().parseFromString(await file.text(), 'image/svg+xml');
        if (svg.querySelector('parsererror') || svg.documentElement.localName !== 'svg') throw new Error(`${file.name} 不是有效的 SVG`);
        project.stageManager.add(new SvgNode(project, {
          attachmentId: project.addAttachment(new Blob([new XMLSerializer().serializeToString(svg)], { type: 'image/svg+xml' })),
          collisionBox: new CollisionBox([new Rectangle(location, Vector.getZero())]),
        }));
      } else {
        project.stageManager.add(new TextNode(project, {
          text: await file.text(), sizeAdjust: 'manual',
          collisionBox: new CollisionBox([new Rectangle(location, new Vector(350, 180))]),
        }));
      }
      changed = true;
    }
  } finally {
    if (changed) { project.historyManager.recordStep(); project.renderer.tick(); }
  }
}

function dataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

/** Reuse the upstream SVG renderer's public extension point. Its default string
 * export draws image placeholders; browser downloads need self-contained data URLs. */
class BrowserSvgExport extends StageExportSvg {
  constructor(project: Project, private images: Map<string, string>) { super(project); }
  override dumpImageNode(node: ImageNode, config: SvgExportConfig) {
    const href = this.images.get(node.attachmentId);
    if (!href || node.isHiddenBySectionCollapse) return super.dumpImageNode(node, config);
    const rect = node.rectangle;
    return <g><image href={href} x={rect.leftTop.x} y={rect.leftTop.y} width={rect.size.x} height={rect.size.y} />{super.dumpImageNode(node, config)}</g>;
  }
}
async function canvasSvg(project: Project, selected: boolean) {
  if (selected && !project.stageManager.getSelectedEntities().length) throw new Error('请先选中要导出的内容');
  const images = new Map<string, string>();
  // A selected section includes its descendants in the upstream SVG renderer.
  const entities = selected
    ? project.sectionMethods.getAllEntitiesInSelectedSectionsOrEntities(project.stageManager.getSelectedEntities())
    : project.stageManager.getEntities();
  const nodes = entities.filter(node => node instanceof ImageNode || node instanceof SvgNode);
  for (const node of nodes) {
    const blob = project.attachments.get(node.attachmentId);
    if (blob && !images.has(node.attachmentId)) images.set(node.attachmentId, await dataUrl(blob));
  }
  const exporter = new BrowserSvgExport(project, images);
  const svg = new DOMParser().parseFromString(selected ? exporter.dumpSelectedToSVGString() : exporter.dumpStageToSVGString(), 'image/svg+xml');
  // Upstream string export omits SvgNode. Preserve these vector attachments in
  // the download as self-contained SVG images, using their public geometry.
  for (const node of nodes) {
    if (!(node instanceof SvgNode) || node.isHiddenBySectionCollapse) continue;
    const href = images.get(node.attachmentId);
    if (!href) continue;
    const rect = node.collisionBox.getRectangle(), image = svg.createElementNS('http://www.w3.org/2000/svg', 'image');
    for (const [name, value] of Object.entries({ href, x: rect.location.x, y: rect.location.y, width: rect.size.x, height: rect.size.y })) image.setAttribute(name, String(value));
    svg.documentElement.append(image);
  }
  return new XMLSerializer().serializeToString(svg);
}

export async function exportCanvasFile(project: Project, selected: boolean, format: 'svg' | 'png') {
  const svg = await canvasSvg(project, selected);
  let blob = new Blob([svg], { type: 'image/svg+xml' });
  if (format === 'png') {
    const url = URL.createObjectURL(blob);
    try {
      const image = new Image();
      image.src = url; await image.decode();
      const width = Math.ceil(image.naturalWidth), height = Math.ceil(image.naturalHeight);
      if (!width || !height || width > 16384 || height > 16384 || width * height > 64 * 1024 * 1024) throw new Error('画布过大，请缩小选区或导出 SVG');
      const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
      const context = canvas.getContext('2d');
      if (!context) throw new Error('无法创建 PNG 画布');
      context.fillStyle = project.stageStyleManager.currentStyle.Background.toString();
      context.fillRect(0, 0, width, height); context.drawImage(image, 0, 0);
      blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('PNG 导出失败')), 'image/png'));
    } finally { URL.revokeObjectURL(url); }
  }
  downloadBlob(blob, `${project.title.replace(/\.prg$/i, '')}${selected ? '-选中' : ''}.${format}`);
}
