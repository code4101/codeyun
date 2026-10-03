import type { Project } from '@/core/Project';
import { CopyEngineText } from '@/core/service/dataManageService/copyEngine/copyEngineText';
import mime from 'mime';
import { toast } from 'sonner';
import { captureStage } from './graphDocument';
import { clipboardHtml, parseClipboardHtml, selectionObjects, attachmentIds, preparePaste, insertPaste, removeCut,
  type GraphClipboard } from '../../../frontend/src/plugins/modules/project-graph/graphClipboard.ts';
import type { Objects } from './graphObjects';

/** Clipboard ownership outlives the iframe; native text editing keeps its clipboard. */
export class GraphClipboardController {
  private busy = false;
  constructor(private project: Project, private host: (type: string, payload?: unknown) => Promise<any>,
    private options: { active: () => boolean; readOnly: () => boolean; edit: (change: (before: Objects) => Objects) => Promise<void> }) {}

  private async run(action: () => Promise<void>) {
    if (this.busy) throw new Error('剪贴板操作正在进行，请稍候');
    this.busy = true;
    try { await action(); } finally { this.busy = false; }
  }
  async copy(cut = false) {
    return this.run(async () => {
      if (cut && this.options.readOnly()) throw new Error('当前文件为只读');
      const objects = selectionObjects(captureStage(this.project), this.project.stage.filter(item => item.isSelected).map(item => item.uuid));
      const attachments: GraphClipboard['attachments'] = {};
      for (const id of attachmentIds(objects)) {
        const blob = this.project.attachments.get(id);
        if (!blob) throw new Error('选中对象的附件缺失，未复制或剪切');
        const bytes = new Uint8Array(await blob.arrayBuffer()); let data = '';
        for (let i = 0; i < bytes.length; i += 8192) data += String.fromCharCode(...bytes.subarray(i, i + 8192));
        attachments[id] = { type: blob.type, data: btoa(data) };
      }
      const packet: GraphClipboard = { format: 'codeyun.pg-selection', version: 1, objects, attachments,
        tags: [...this.project.tags], text: objects['@order'].value.map((id: string) => objects[id].text ?? '').filter(Boolean).join('\n\n') };
      await this.host('clipboard-write', packet);
      const html = clipboardHtml(packet);
      {
        try {
          await navigator.clipboard.write([new ClipboardItem({ 'text/plain': new Blob([packet.text], { type: 'text/plain' }),
            'text/html': new Blob([html], { type: 'text/html' }) })]);
        } catch (error) {
          if (cut) throw new Error('系统剪贴板写入失败，原图未剪切。请允许剪贴板访问后重试。', { cause: error });
          toast.info('已复制图结构；浏览器未允许写入系统剪贴板，可在当前工作台内粘贴');
        }
      }
      if (cut) await this.options.edit(before => removeCut(before, packet));
      toast.success(cut ? '已剪切图结构' : '已复制图结构');
    });
  }
  async paste(origin = false, event?: ClipboardEvent) {
    return this.run(async () => {
      if (this.options.readOnly()) throw new Error('当前文件为只读');
      let packet: GraphClipboard | null = null, text = '';
      if (event?.clipboardData) {
        packet = parseClipboardHtml(event.clipboardData.getData('text/html'));
        text = event.clipboardData.getData('text/plain');
      } else {
        let read = false;
        try {
          const items = await navigator.clipboard.read(); read = true;
          for (const item of items) {
            if (item.types.includes('text/html')) packet = parseClipboardHtml(await (await item.getType('text/html')).text());
            if (item.types.includes('text/plain')) text = await (await item.getType('text/plain')).text();
            if (packet) break;
          }
        } catch (error) {
          // Malformed graph data must fail, never silently become plain nodes.
          if (read) throw error;
          packet = await this.host('clipboard-read');
          if (!packet) { await this.project.copyEngine.readSystemClipboardAndPaste(); return; }
        }
      }
      if (!packet) {
        if (text) await new CopyEngineText(this.project).copyEnginePastePlainText(text);
        else await this.project.copyEngine.readSystemClipboardAndPaste();
        return;
      }
      const point = this.project.camera.location;
      const prepared = preparePaste(packet, origin ? undefined : { x: point.x, y: point.y });
      await this.options.edit(before => insertPaste(before, prepared, type => mime.getExtension(type)));
      const ids = new Set(prepared.objects['@order'].value as string[]);
      for (const item of this.project.stage) item.isSelected = ids.has(item.uuid);
      this.project.controllerUtils.selectedEntityNormalizing(); this.project.renderer.tick();
      toast.success('已粘贴图结构');
    });
  }
  private isCanvasTarget(event: Event) {
    if (!this.options.active()) return false;
    const element = event.target instanceof HTMLElement ? event.target : document.activeElement;
    return !(element instanceof HTMLElement && (element.isContentEditable || element.closest('input,textarea,select,[role="textbox"]')));
  }
  private keydown = (event: KeyboardEvent) => {
    if (!this.isCanvasTarget(event) || !(event.ctrlKey || event.metaKey) || event.altKey) return;
    const key = event.key.toLowerCase();
    if (!['c', 'x', 'v'].includes(key)) return;
    event.preventDefault(); event.stopImmediatePropagation();
    void (key === 'v' ? this.paste(event.shiftKey) : this.copy(key === 'x')).catch(error => toast.error(String(error)));
  };
  // Paste events supply rich data even when navigator.clipboard.read is denied.
  private pasteEvent = (event: ClipboardEvent) => {
    if (!this.isCanvasTarget(event)) return;
    event.preventDefault(); event.stopImmediatePropagation();
    void this.paste(false, event).catch(error => toast.error(String(error)));
  };
  install() { document.addEventListener('keydown', this.keydown, true); document.addEventListener('paste', this.pasteEvent, true); }
  dispose() { document.removeEventListener('keydown', this.keydown, true); document.removeEventListener('paste', this.pasteEvent, true); }
}
