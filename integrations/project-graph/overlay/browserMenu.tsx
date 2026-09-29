import { toast } from 'sonner';
import i18next from 'i18next';
import { Settings } from '@/core/service/Settings';
import { KeyBindsUI } from '@/core/service/controlService/shortcutKeysEngine/KeyBindsUI';
import { Project } from '@/core/Project';
const canvasSwitches = {
  isStealthModeEnabled: '聚光灯模式', stealthModeReverseMask: '反转聚光灯遮罩',
  forceHideTextNodeBorder: '隐藏文本节点边框', alwaysShowDetails: '始终显示节点正文',
  showDebug: '显示调试信息', enableDragAutoAlign: '拖动自动对齐',
  reverseTreeMoveMode: '反转树移动模式', textIntegerLocationAndSizeRender: '文字位置和尺寸取整',
  showQuickSettingsToolbar: '显示画布快捷工具栏',
} as const;
function canvasSwitch(id: string) {
  const key = id.startsWith('canvas-setting:') ? id.slice(15) : '';
  return Object.hasOwn(canvasSwitches, key) ? key as keyof typeof canvasSwitches : undefined;
}
// Omit desktop-only housekeeping; useful but unavailable features keep a disabled
// entry with an on-demand explanation in the host menu.
const desktopOnlyCommands = new Set(`openConfigFolder openCacheFolder openCustomBackupFolder
openDefaultBackupFolder openExtensionFolder checkoutWindowOpacityMode windowOpacityAlphaDecrease
windowOpacityAlphaIncrease checkoutProtectPrivacy`.split(/\s+/));
const nativeCommands = new Set(`resetViewAll resetView resetCameraScale moveViewToOrigin stopDrifting focusRandomEntity
searchText undo redo releaseKeys closeAllSubWindows generateNodeTreeByText generateNodeTreeByMarkdown
generateNodeGraphByText generateNodeMermaidByText openLogicNodePanel openLogicNodeDocs clearStage
autoNamerTemplate autoNamerSectionTemplate autoNamerDetailsTemplate autoNamerTreeNodeTemplate
autoFillNodeColorToggle autoFillNodeColorSet clickTagPanelButton openOutlineWindow openColorManagerWindow
toggleBackgroundHorizontalLines toggleBackgroundVerticalLines toggleBackgroundDots toggleBackgroundCartesian
switchDebugShow switchStealthMode toggleStealthModeReverseMask stealthModeScopeRadiusIncrease stealthModeScopeRadiusDecrease
exportSelectedNetStructureToPlainText exportSelectedTreeStructureToPlainText exportSelectedTreeStructureToMarkdown
exportSelectedNetStructureToMermaid generateKeyboardLayout openOfficialDocs
watchBilibiliVideo2 watchBilibiliVideo1_6Basic watchBilibiliVideo1_6Advanced watchBilibiliVideo1_0
watchBilibiliVideoPyQtUpdated watchBilibiliVideoPyQt showUpgradeGuide`.split(/\s+/));
if (/^https?:\/\//.test(import.meta.env.LR_API_BASE_URL ?? '')) nativeCommands.add('openPluginMarket');

const unavailable: Record<string, string> = {};
function explain(ids: string, reason: string) { for (const id of ids.split(/\s+/)) unavailable[id] = reason; }
explain('openAIPanel', '尚未接入网页 AI 会话与模型服务');
explain('openAITools', '桌面 MCP、Skills 运行环境尚未迁移');
explain('openExtensionsWindow openExtensionFolder', '依赖桌面扩展运行环境');
explain('openPluginMarket', '尚未配置原版扩展市场地址');
explain('openConfigFolder openCacheFolder openCustomBackupFolder openDefaultBackupFolder', '网页文件由资源管理器管理，无桌面目录');
explain('startCollaboration joinCollaboration openCollaborationPanel leaveCollaboration', '请在资源管理器的文件菜单中设置分享并开启协作');
explain('checkoutWindowOpacityMode windowOpacityAlphaDecrease windowOpacityAlphaIncrease checkoutProtectPrivacy', '需要桌面窗口能力');
explain('checkoutClassroomMode', '尚未适配网页工作台布局');
explain('openReferencesWindow updateReferences', '跨文件引用尚未接入网页资源库');
explain('exportCurrentViewPrgDeepLink exportSelectedEntityPrgDeepLink', '网页尚未支持带定位的分享链接');
explain('openBackgroundManagerWindow', '背景文件管理尚未迁移');
explain('importFromFolder importTreeFromFolder upgradeOldJson', '此导入流程尚未迁移');

type Item = { id: string; type: string; label?: string; visible?: boolean; children?: Item[] };
type Actions = Record<string, () => void | Promise<void>>;
// A viewer can navigate, inspect and export. Unknown future commands default to
// disabled, so upgrading upstream cannot silently introduce a new mutation path.
const viewerCommands = new Set(`resetViewAll resetView resetCameraScale moveViewToOrigin stopDrifting focusRandomEntity
searchText releaseKeys closeAllSubWindows openOutlineWindow nodeDetails openAppearanceSettings toggleFullscreen
manualBackup downloadCanvas sourceCode openAboutWindow newPrgAtCurrentDir newDraft openFile openCurrentProjectFileFolder saveAs
exportSelectedNetStructureToPlainText exportSelectedTreeStructureToPlainText exportSelectedTreeStructureToMarkdown
exportSelectedNetStructureToMermaid`.split(/\s+/));
let readOnly = false;
export function configureReadOnly(value: boolean) {
  readOnly = value;
  Settings.viewerMode = value;
  if (value) KeyBindsUI.onKeyBindListChange(bindings => {
    for (const binding of bindings) if (!viewerCommands.has(binding.id)) binding.isEnabled = false;
  });
}
const labels: Record<string, string> = { newPrgAtCurrentDir: '新建.prg', openFile: '导入 .prg',
  openCurrentProjectFileFolder: '资源管理器', saveFile: '保存', saveAs: '另存为副本', manualBackup: '下载 .prg',
  clickAppMenuSettingsButton: '配置', openAppearanceSettings: '主题', nodeDetails: '正文',
  downloadCanvas: '当前视野 PNG', openAboutWindow: 'Project Graph · GPL-3.0', sourceCode: '下载对应源码' };
Object.assign(labels, { newDraft: '新建图文件（资源库）', recentFilesEntries: '在资源管理器中浏览',
  exportCurrentFilePrgDeepLink: '复制当前文件网页链接', openAITools: 'AI 工具（内置工具目录）',
  importTextFile: '导入文本文件为节点', exportPngLegacy: '导出整个画布 PNG' });

export function installBrowserCommands(actions: Actions) {
  for (const [id, action] of Object.entries(actions)) {
    const previous = KeyBindsUI.getUIKeyBind(id);
    KeyBindsUI.registerOneUIKeyBind(id, previous?.key ?? '', true,
      () => { void Promise.resolve().then(action).catch(error => toast.error(String(error))); },
      undefined, false, () => true, previous?.icon);
  }
}

export async function browserMenuModel(project: Project, actions: Actions) {
  const prune = (items: Item[]): Item[] => items.flatMap(item => {
    if (item.visible === false || desktopOnlyCommands.has(item.id)) return [];
    if (item.children) {
      const children = prune(item.children);
      return children.length ? [{ ...item, children }] : [];
    }
    return [item];
  }).filter((item, index, list) => item.type !== 'separator' ||
    (index > 0 && index < list.length - 1 && list[index - 1].type !== 'separator'));
  const config = prune(Settings.globalMenuConfig as Item[]);
  config.find(item => item.id === 'view')?.children?.push({ type: 'submenu', id: 'canvas-settings', label: '画布快捷设置',
    children: Object.keys(canvasSwitches).map(key => ({ type: 'item', id: `canvas-setting:${key}` })) });
  config.find(item => item.id === 'file')?.children?.push({ type: 'item', id: 'downloadCanvas' });
  config.find(item => item.id === 'window')?.children?.unshift({ type: 'item', id: 'nodeDetails' });
  config.find(item => item.id === 'about')?.children?.push({ type: 'item', id: 'sourceCode' });
  async function resolve(item: Item): Promise<unknown> {
    const setting = canvasSwitch(item.id);
    if (setting) return { id: item.id, label: `${Settings[setting] ? '✓ ' : ''}${canvasSwitches[setting]}`, disabled: false };
    const unsupported = !item.children && item.type !== 'separator' && !actions[item.id] && !nativeCommands.has(item.id);
    return { id: item.id, label: labels[item.id] ?? i18next.t(`${item.id}.title`, { ns: 'keyBinds', defaultValue: item.label ?? item.id }),
      separator: item.type === 'separator', disabled: unsupported || (readOnly && item.type === 'item' && !viewerCommands.has(item.id)) || (item.type === 'item' && !actions[item.id] && !(await KeyBindsUI.canExecute(item.id, project))),
      disabledReason: unsupported ? unavailable[item.id] ?? '此功能尚未迁移到网页端' : undefined,
      children: item.children ? await Promise.all(item.children.map(resolve)) : undefined };
  }
  return Promise.all(config.map(resolve));
}
export async function executeBrowserCommand(id: string, project: Project, actions: Actions) {
  const setting = canvasSwitch(id);
  if (setting) { Settings[setting] = !Settings[setting]; project.renderer.tick(); return; }
  if (readOnly && !viewerCommands.has(id)) return;
  if (!actions[id] && !nativeCommands.has(id)) { toast.info(unavailable[id] ?? '此功能尚未迁移到网页端'); return; }
  project.controller.pressingKeySet.clear();
  if (actions[id]) await actions[id]();
  else await KeyBindsUI.execute(id, project);
  project.controller.resetCountdownTimer();
  project.renderer.tick();
}
