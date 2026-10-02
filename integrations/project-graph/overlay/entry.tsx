import { configureSharedKeyboardHints } from './sharedKeyboardHints';
import { browserMenuModel, executeBrowserCommand, installBrowserCommands, configureReadOnly } from './browserMenu';
import { importCanvasFiles, exportCanvasFile, downloadBlob } from './browserFileCommands';
import { AssetsRepository } from '@/core/service/AssetsRepository';
import { openBrowserPanel } from './browserPanels';
import { Dialog } from '@/components/ui/dialog';
import { Color } from '@graphif/data-structures';
import { installKeyboardLifecycle } from './keyboardLifecycle';
import { PersistentCamera } from './persistentCamera';
import { createRoot } from 'react-dom/client';
import { Provider } from 'jotai';
import i18next from 'i18next';
import { initReactI18next } from 'react-i18next';
import { URI } from 'vscode-uri';
import { toast } from 'sonner';
import { Toaster } from '@/components/ui/sonner';
import DockedArea from '@/components/docked-area';
import FloatingTabs from '@/components/floating-tabs';
import RenderOverlays from '@/components/overlay-host';
import MyContextMenuContent from '@/components/context-menu-content';
import { ContextMenu, ContextMenuTrigger } from '@/components/ui/context-menu';
import { Project, ProjectState } from '@/core/Project';
import { TabWorkspace } from '@/core/TabWorkspace';
import { loadAllServicesBeforeInit, loadAllServicesAfterInit } from '@/core/loadAllServices';
import { Settings } from '@/core/service/Settings';
import { Themes } from '@/core/service/Themes';
import { ColorManager } from '@/core/service/feedbackService/ColorManager';
import { StageStyle } from '@/core/service/feedbackService/stageStyle/stageStyle';
import { QuickSettingsManager } from '@/core/service/QuickSettingsManager';
import { MouseLocation } from '@/core/service/controlService/MouseLocation';
import { KeyBindsUI } from '@/core/service/controlService/shortcutKeysEngine/KeyBindsUI';
import { EdgeCollisionBoxGetter } from '@/core/stage/stageObject/association/EdgeCollisionBoxGetter';
import { store, tabsAtom, activeTabAtom } from '@/state';
import { setSubWindowOpenMode } from '@/core/subWindowOpen';
import { openBrowserSettings } from './browserSettings';
import { FollowingControllerUtils, SelectionDetailsService, configureDetails, configureDetailsAccess, updateNodeDetails } from './selectionDetails';
import { ObjectCollaboration } from './collaboration';
import { BodyPreviewRenderer } from './bodyPreviewRenderer';
import { readGallery, writeGallery, galleryGeneration, galleryObjects } from './galleryArchive';
import { GalleryHistory } from './galleryHistory';
import { installGalleryCanvasDrag } from './galleryCanvasDrag';
import { captureStage, applyObjects } from './graphDocument';
import { changeGallery, gallerySnapshot, type GalleryCommand } from '../../../frontend/src/plugins/modules/project-graph/gallery.ts';
import '@/css/index.css';
import './embed.css';

const channel = 'codeyun.project-graph';
const session = new URLSearchParams(location.search).get('session');
const pending = new Map<string, { resolve: (value: any) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }>();
function send(type: string, payload: unknown = {}, id?: string) {
  parent.postMessage({ channel, version: 1, session, type, payload, id }, location.origin);
}
function request(type: string, payload: unknown = {}): Promise<any> {
  const id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('宿主响应超时，请重试或导出备份')); }, 15000);
    pending.set(id, { resolve, reject, timer });
    send(type, payload, id);
  });
}
let project: Project;
let readOnly = false;
let galleryVisible = false, galleryBusy = false, lastGallery = '';
function publishGallery(force = false) {
  if (!project || (!galleryVisible && !force)) return;
  const selected = project.stage.filter(item => item.isSelected);
  // Tool updates carry only a directory/selection summary. Never serialize all
  // stage geometry, bodies or attachment bytes just to refresh the sidebar.
  const values = { ...galleryObjects(project), ...Object.fromEntries(selected.map(item => [item.uuid, { text: 'text' in item ? item.text : '' }])) };
  const snapshot = gallerySnapshot(values, selected.map(item => item.uuid), readOnly);
  const key = JSON.stringify(snapshot);
  if (force || key !== lastGallery) { lastGallery = key; send('gallery-state', snapshot); }
}
async function galleryCommand(command: GalleryCommand) {
  if (!project || readOnly) throw new Error('当前画布为只读');
  if (galleryBusy) throw new Error('图库正在保存，请稍候');
  galleryBusy = true;
  try {
    const restoredIds: string[] = command.action === 'take'
      ? galleryObjects(project)['@gallery:item:' + command.itemId]?.objects?.['@order']?.value ?? [] : [];
    if (collaboration) await collaboration.editGallery(command);
    else {
      project.historyManager.recordStep();
      const before = captureStage(project), next = changeGallery(before, command);
      applyObjects(project, before, next);
      project.historyManager.recordStep();
      do { await save(); } while (fingerprint() !== lastSaved);
    }
    if (command.action === 'take') {
      const ids = new Set(restoredIds);
      for (const item of project.stage) item.isSelected = ids.has(item.uuid);
      // Taking a subgraph changes document contents, not the user's viewport.
      // Keep its zoom and location; explicit camera commands still fit selection.
      project.renderer.tick();
    }
    publishGallery(true);
  } finally { galleryBusy = false; }
}
function sendCanvasMode() {
  send('canvas-mode', { mode: Settings.mouseLeftMode, readOnly, color: Settings.autoFillPenStrokeColor });
}

let collaboration: ObjectCollaboration | undefined;
type HostTheme = { background: string; panel: string; text: string; border: string; accent: string; dark: boolean };
let hostTheme: HostTheme = { background: '#fff', panel: '#f5f7fa', text: '#303133', border: '#e4e7ed', accent: '#409eff', dark: false };
let themeReady = false;
let themeQueue = Promise.resolve();
function applyHostTheme() {
  themeQueue = themeQueue.then(async () => {
    const theme = hostTheme;
    const id = theme.dark ? 'dark' : 'light';
    await Themes.applyThemeById(id);
    const root = document.documentElement;
    for (const [key, value] of Object.entries({ background: theme.background, foreground: theme.text,
      card: theme.panel, 'card-foreground': theme.text, popover: theme.panel, 'popover-foreground': theme.text,
      muted: theme.panel, accent: theme.panel, 'accent-foreground': theme.text,
      border: theme.border, input: theme.border, ring: theme.accent, brand: theme.accent })) root.style.setProperty(`--${key}`, value);
    root.classList.toggle('dark', theme.dark);
    if (themeReady) {
      const style = await StageStyle.styleFromTheme(id);
      style.Background = Color.fromCss(theme.background);
      style.StageObjectBorder = Color.fromCss(theme.text);
      style.NodeDetailsText = Color.fromCss(theme.text);
      style.GridNormal = Color.fromCss(theme.border);
      style.GridHeavy = Color.fromCss(theme.border);
      style.DetailsDebugText = Color.fromCss(theme.text);
      style.CollideBoxSelected = Color.fromCss(theme.accent);
      project.stageStyleManager.currentStyle = style;
    }
  }).catch(report);
  return themeQueue;
}
// Adapt file and workspace commands at the boundary, keeping database ownership in Vue.
const hostCommand = (command: string) => send('host-command', { command });
const menuActions = {
  resetAllKeyBinds: resetBrowserKeybinds,
  openAttachmentsWindow: () => openBrowserPanel(project, 'attachments'),
  openAITools: () => openBrowserPanel(project, 'ai-tools'),
  newDraft: () => hostCommand('new'),
  recentFilesEntries: () => hostCommand('files'),
  clickAppMenuRecentFileButton: () => hostCommand('files'),
  importImages: () => importCanvasFiles(project, 'image'),
  importSvg: () => importCanvasFiles(project, 'svg'),
  importTextFile: () => importCanvasFiles(project, 'text'),
  exportSvgAll: () => exportCanvasFile(project, false, 'svg'),
  exportSvgSelected: () => exportCanvasFile(project, true, 'svg'),
  exportPngLegacy: () => exportCanvasFile(project, false, 'png'),
  exportPngSelected: () => exportCanvasFile(project, true, 'png'),
  exportCurrentFilePrgDeepLink: async () => { await navigator.clipboard.writeText(parent.location.href); toast.success('已复制当前文件链接'); },
  downloadTutorialMain: () => downloadTutorial('tutorial-main-3.2.prg'),
  downloadTutorialShortcutKeys: () => downloadTutorial('tutorial-shortcut-keys-3.2.prg'),
  downloadTutorialLogicNodes: () => downloadTutorial('tutorial-logic-nodes-2.9.prg'),
  newPrgAtCurrentDir: () => hostCommand('new'),
  openFile: () => hostCommand('import'),
  openCurrentProjectFileFolder: () => hostCommand('files'),
  saveFile: () => hostCommand('save'),
  saveAs: () => hostCommand('copy'),
  manualBackup: () => hostCommand('download'),
  clickAppMenuSettingsButton: () => openBrowserSettings(project),
  openAppearanceSettings: () => hostCommand('settings'),
  nodeDetails: () => hostCommand('details'),
  toggleFullscreen: () => hostCommand('fullscreen'),
  openAboutWindow: () => { window.open('/plugins/project-graph/LICENSE.txt', '_blank', 'noopener,noreferrer'); },
  sourceCode: () => { window.open('/plugins/project-graph/source.zip', '_blank', 'noopener,noreferrer'); },
  downloadCanvas: async () => {
    const canvas = [...document.querySelectorAll('canvas')].sort((a, b) => b.width * b.height - a.width * a.height)[0];
    if (!canvas) return;
    project.renderer.tick();
    const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('画布导出失败')), 'image/png'));
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = `${project.title.replace(/\.prg$/i, '')}.png`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  },
};
async function resetBrowserKeybinds(): Promise<void> {
  if (!await Dialog.confirm('重置快捷键', '将所有快捷键恢复为默认值？', { destructive: true })) return;
  await KeyBindsUI.resetAllKeyBinds();
  // Upstream resets command implementations too; retain browser file handlers.
  installBrowserCommands(menuActions);
  configureReadOnly(readOnly);
  toast.success('快捷键已重置');
}
async function downloadTutorial(name: string) {
  const response = await fetch(AssetsRepository.getGuideFileUrl(`tutorials/${name}`));
  if (!response.ok) throw new Error(`教程下载失败：${response.status}`);
  downloadBlob(await response.blob(), name);
}
let initialBytes: Uint8Array | null = null;
let lastSaved = '';
let saving: Promise<void> | null = null;
let failure = false;
function fingerprint() {
  return project.stageHash + JSON.stringify([galleryGeneration(project), project.tags, project.references, project.readme, [...project.attachments].map(([id, blob]) => [id, blob.size])]);
}
function report(error: unknown) { const message = String(error); toast.error(message); send('error', { message }); }

/** Public FileSystemProvider contract only; the host owns storage and conflict detection. */
class HostFiles {
  async exists() { return initialBytes !== null; }
  async read() { if (!initialBytes) throw new Error('文档不存在'); return initialBytes; }
  async write(_uri: URI, bytes: Uint8Array) { await request('write', { bytes }); }
  async readDir() { return []; }
  async remove() { throw new Error('请在宿主中删除文档'); }
  async mkdir() { throw new Error('嵌入文档不支持目录操作'); }
  async rename() { throw new Error('请在宿主中重命名文档'); }
}

async function save() {
  if (collaboration) return collaboration.flush();
  if (!project || readOnly) return;
  if (saving) return saving;
  saving = (async () => {
    const before = fingerprint();
    if (before === lastSaved) return;
    send('status', { state: 'saving' });
    const bytes = await project.getFileContent({ includeThumbnail: false });
    await project.fs.write(project.uri, bytes);
    // Edits during compression/persistence must never be marked as saved by an older response.
    lastSaved = before;
    failure = false;
    const unchanged = fingerprint() === before;
    project.projectState = unchanged ? ProjectState.Saved : ProjectState.Unsaved;
    send('status', { state: unchanged ? 'saved' : 'unsaved' });
  })().catch(error => { failure = true; report(error); throw error; }).finally(() => { saving = null; });
  return saving;
}

window.addEventListener('message', async event => {
  const message = event.data;
  if (event.source !== parent || event.origin !== location.origin || message?.channel !== channel || message.version !== 1 || message.session !== session) return;
  if (message.type === 'response') {
    const item = pending.get(message.id);
    if (item) { clearTimeout(item.timer); pending.delete(message.id); message.error ? item.reject(new Error(message.error)) : item.resolve(message.payload); }
    return;
  }
  try {
    if (message.type === 'gallery-visible') { galleryVisible = !!message.payload.active; publishGallery(true); }
    if (message.type === 'gallery-command') {
      try { await galleryCommand(message.payload); send('gallery-result', {}, message.id); }
      catch (error) { send('gallery-result', { error: String(error) }, message.id); publishGallery(true); }
    }
    if (message.type === 'theme') { hostTheme = message.payload; await applyHostTheme(); }
    if (message.type === 'flush') {
      (project.camera as PersistentCamera).saveView();
      try {
        if (!project) throw new Error('编辑器尚未就绪');
        if (collaboration) await collaboration.flush();
        else if (!readOnly) do { await save(); } while (fingerprint() !== lastSaved);
        send('flushed', { id: message.id });
      } catch (error) { send('flushed', { id: message.id, error: String(error) }); }
    }
    if (message.type === 'aux-focus' && project) TabWorkspace.focus(message.payload.id || project.id);
    if (message.type === 'aux-close' && project && message.payload.id !== project.id) await TabWorkspace.close(message.payload.id);
    if (message.type === 'menu-request' && project) send('menu-model', await browserMenuModel(project, menuActions));
    if (message.type === 'menu-execute' && project) await executeBrowserCommand(message.payload.id, project, menuActions);
    if (message.type === 'shared-toolbar') { document.documentElement.classList.toggle('codeyun-shared-toolbar', !!message.payload.active); configureSharedKeyboardHints(!!message.payload.active); project?.controller.resetCountdownTimer(); project?.renderer.tick(); }
    if (message.type === 'canvas-mode-request') sendCanvasMode();
    if (message.type === 'canvas-mode-set' && !readOnly) {
      if (['selectAndMove', 'draw', 'connectAndCut'].includes(message.payload.mode)) Settings.mouseLeftMode = message.payload.mode;
      if (Array.isArray(message.payload.color) && message.payload.color.length === 4 && message.payload.color.every((v: unknown) => typeof v === 'number' && Number.isFinite(v))) Settings.autoFillPenStrokeColor = message.payload.color;
      sendCanvasMode();
    }
    if (message.type === 'save') await save();
    if (message.type === 'details-visible') configureDetails(Boolean(message.payload.active));
    if (message.type === 'details-change' && project && !readOnly) {
      const edit = () => updateNodeDetails(project, message.payload.id, message.payload.value);
      if (collaboration) await collaboration.editDetails(message.payload.id, edit); else edit();
    }
    if (message.type === 'export' && project) send('exported', { bytes: await project.getFileContent({ includeThumbnail: false }) });
  } catch (error) { if (message.type !== 'save') report(error); }
});

async function boot() {
  if (parent === window || !session) throw new Error('请从 CodeYun 绘图体验页打开编辑器');
  const result = await request('ready', { capabilities: ['prg', 'node-details', 'export'], upstream: '991be19' });
  readOnly = result.readOnly === true;
  document.documentElement.classList.toggle('codeyun-shared-toolbar', !!result.sharedToolbar);
  configureSharedKeyboardHints(!!result.sharedToolbar, value => send('keyboard-hints', value));
  Settings.watch('mouseLeftMode', sendCanvasMode);
  Settings.watch('autoFillPenStrokeColor', sendCanvasMode);
  sendCanvasMode();
  document.documentElement.classList.toggle('codeyun-readonly', readOnly);
  if (result.theme) hostTheme = result.theme;
  configureDetails(Boolean(result.detailsActive), payload => send('selection-details', payload));
  initialBytes = result.bytes ? new Uint8Array(result.bytes) : null;
  await i18next.use(initReactI18next).init({ lng: 'zh_CN', defaultNS: '', resources: { zh_CN: (await import('@/locales/zh_CN.yml')).default } });
  Settings.autoSave = false;
  Settings.autoBackup = false;
  Settings.telemetry = false;
  // Move the canvas switches into the menu once; later toolbar choices persist.
  if (!localStorage.getItem('codeyun.pg.menu-quick-settings.v1')) {
    Settings.showQuickSettingsToolbar = false;
    localStorage.setItem('codeyun.pg.menu-quick-settings.v1', '1');
  }
  await Promise.all([ColorManager.init(), QuickSettingsManager.init()]);
  await applyHostTheme();
  EdgeCollisionBoxGetter.init();
  MouseLocation.init();
  await KeyBindsUI.registerAllUIKeyBinds();
  installBrowserCommands(menuActions);
  configureReadOnly(readOnly);
  KeyBindsUI.uiStartListen();
  for (const id of ['FindWindow', 'TagWindow', 'OutlineWindow', 'LogicNodePanel', 'ColorManagerPanel', 'GenerateNodeTree', 'GenerateNodeTreeByMarkdown', 'GenerateNodeGraph', 'GenerateNodeMermaid'] as const) setSubWindowOpenMode(id, 'docked');
  createRoot(document.getElementById('root')!).render(
    <Provider store={store}>
      <Toaster richColors />
      <ContextMenu><ContextMenuTrigger asChild><div className="fixed inset-0 bg-background text-foreground">

          <div className="codeyun-docked absolute inset-0">
            <DockedArea onTabClick={tab => TabWorkspace.focus(tab.id)} onTabClose={tab => { if (tab !== project) void TabWorkspace.close(tab.id); }} isClassroomMode={false} />
          </div>

        <FloatingTabs onTabClose={tab => TabWorkspace.close(tab.id)} />
      </div></ContextMenuTrigger>{!readOnly && <MyContextMenuContent />}</ContextMenu>
      <RenderOverlays />
    </Provider>,
  );
  class EmbeddedProject extends Project {
    get title() { return result.title || '绘图文档'; }
    async save() { await save(); }
    override async getFileContent(options: { includeThumbnail?: boolean } = {}) {
      const extension = galleryObjects(this);
      return writeGallery(this, await super.getFileContent(options), extension);
    }
  }
  project = new EmbeddedProject(URI.parse('codeyun:/document.prg'));
  await readGallery(project, initialBytes);
  project.closable = false;
  loadAllServicesBeforeInit(project);
  project.disposeService('entityRenderer');
  project.loadService(BodyPreviewRenderer);
  project.disposeService('camera');
  project.loadService(PersistentCamera);
  if (typeof result.viewStateKey === 'string') (project.camera as PersistentCamera).configure(result.viewStateKey);
  project.disposeService('controllerUtils');
  project.loadService(FollowingControllerUtils);
  project.stageStyleManager.currentStyle = await StageStyle.styleFromTheme(hostTheme.dark ? 'dark' : 'light');
  project.disposeService('autoSaveBackup');
  project.registerFileSystemProvider('codeyun', HostFiles);
  await project.init();
  if (initialBytes && project.projectState !== ProjectState.Saved) throw new Error('文档打开未完成，原文档保持不变');
  loadAllServicesAfterInit(project);
  project.disposeService('historyManager'); project.loadService(GalleryHistory);
  if (result.collaboration) {
    let initialCredentials = result.collaboration;
    collaboration = new ObjectCollaboration(project, readOnly, async () => {
      if (initialCredentials) { const value = initialCredentials; initialCredentials = null; return value; }
      return request('collaboration-credentials');
    }, `${result.viewStateKey ?? 'codeyun.pg'}:draft`, send);
    configureDetailsAccess(id => collaboration!.access(id));
    window.addEventListener('pagehide', () => collaboration?.dispose(), { once: true });
  }
  themeReady = true;
  await applyHostTheme();
  project.loadService(SelectionDetailsService);
  const galleryDrag = installGalleryCanvasDrag(project, {
    enabled: () => !readOnly && !galleryBusy && store.get(activeTabAtom) === project,
    begin: () => publishGallery(true),
    publish: value => send('gallery-drag', value) });
  const galleryTick = setInterval(() => publishGallery(), 200);
  window.addEventListener('pagehide', () => { clearInterval(galleryTick); galleryDrag.dispose(); }, { once: true });
  // Stored cards use native HTML drops; outgoing graph drags use the pointer bridge.
  document.addEventListener('dragover', event => {
    if (!readOnly && event.dataTransfer?.types.includes('application/x-codeyun-gallery-item')) {
      event.preventDefault(); event.dataTransfer.dropEffect = 'move';
    }
  });
  document.addEventListener('drop', event => {
    const raw = event.dataTransfer?.getData('application/x-codeyun-gallery-item');
    if (!raw || readOnly) return;
    event.preventDefault();
    try { send('gallery-drop', JSON.parse(raw)); } catch { /* Ignore unrelated/invalid drag data. */ }
  });
  const blockGalleryGesture = (event: Event) => { if (galleryBusy) { event.preventDefault(); event.stopImmediatePropagation(); } };
  document.addEventListener('pointerdown', blockGalleryGesture, true);
  document.addEventListener('keydown', blockGalleryGesture, true);
  TabWorkspace.open(project);
  const publishTabs = () => {
    const tabs = store.get(tabsAtom).filter(tab => tab !== project && !tab.closing && tab.layout === 'docked');
    const active = store.get(activeTabAtom);
    send('aux-tabs', { tabs: tabs.map(tab => ({ id: tab.id, title: tab.title })), active: active && tabs.includes(active) ? active.id : '' });
  };
  const stopTabs = store.sub(tabsAtom, publishTabs), stopActive = store.sub(activeTabAtom, publishTabs);
  window.addEventListener('pagehide', () => { stopTabs(); stopActive(); }, { once: true });
  // Parent-window blur does not reliably identify switches between sibling iframes.
  const publishFocus = () => send('canvas-focus');
  window.addEventListener('focus', publishFocus);
  document.addEventListener('pointerdown', publishFocus, true);
  document.addEventListener('keydown', publishFocus, true);
  window.addEventListener('pagehide', () => {
    window.removeEventListener('focus', publishFocus);
    document.removeEventListener('pointerdown', publishFocus, true);
    document.removeEventListener('keydown', publishFocus, true);
  }, { once: true });
  const disposeKeyboard = installKeyboardLifecycle(project);
  window.addEventListener('pagehide', disposeKeyboard, { once: true });
  project.loop();
  window.addEventListener('pagehide', () => (project.camera as PersistentCamera).saveView());
  window.addEventListener('beforeunload', () => (project.camera as PersistentCamera).saveView());
  // A new empty file is still a file: persist it before the host changes folders.
  lastSaved = readOnly || (initialBytes && !project.wasUpgraded) ? fingerprint() : '';
  project.projectState = ProjectState.Saved;
  project.on('state-change', () => { if (project.projectState === ProjectState.Unsaved) send('status', { state: 'unsaved' }); });
  const markDirty = () => {
    if (collaboration) { collaboration.markDirty(); return; }
    if (readOnly) return;
    if (fingerprint() !== lastSaved) { project.projectState = ProjectState.Unsaved; send('status', { state: 'unsaved' }); }
  };
  project.on('stage-commit', markDirty);
  document.addEventListener('input', () => queueMicrotask(markDirty));
  document.addEventListener('pointerup', () => queueMicrotask(markDirty));
  // A browser integration boundary: observe the public document fingerprint, including details.
  // This also catches upstream undo/redo and edits which don't emit a distinct dirty event.
  setInterval(() => { if (!collaboration && !readOnly && !failure && fingerprint() !== lastSaved) void save().catch(() => {}); }, 1000);
  window.addEventListener('beforeunload', event => { if (collaboration ? collaboration.hasUnsaved : !readOnly && (fingerprint() !== lastSaved || saving)) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); event.stopImmediatePropagation(); void save().catch(() => {}); }
    if ((event.ctrlKey || event.metaKey) && ['n', 'o'].includes(event.key.toLowerCase())) {
      event.preventDefault(); event.stopImmediatePropagation(); hostCommand(event.key.toLowerCase() === 'n' ? 'new' : 'import');
    }
  }, true);
  send('menu-model', await browserMenuModel(project, menuActions));
  send('status', { state: initialBytes ? 'saved' : 'unsaved' });
  // The bridge being ready does not mean the new iframe has a themed canvas yet.
  // Keep the host's themed surface visible until layout and a rendered frame exist.
  while (!project.renderer.w || !project.renderer.h) await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
  await applyHostTheme();
  project.renderer.tick();
  await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
  send('presented');
  publishGallery(true);

}
boot().catch(error => {
  document.getElementById('root')!.textContent = `编辑器启动失败：${String(error)}`;
  send('error', { message: String(error) });
});
