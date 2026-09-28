import { toast } from 'sonner';
import i18next from 'i18next';
import { Settings } from '@/core/service/Settings';
import { KeyBindsUI } from '@/core/service/controlService/shortcutKeysEngine/KeyBindsUI';
import { Project } from '@/core/Project';
// Keep the upstream hierarchy and command implementations. Only capabilities with
// browser contracts belong here; desktop paths, processes and credentials do not.
const nativeCommands = new Set(`resetViewAll resetView resetCameraScale moveViewToOrigin stopDrifting focusRandomEntity
searchText undo redo releaseKeys closeAllSubWindows generateNodeTreeByText generateNodeTreeByMarkdown
generateNodeGraphByText generateNodeMermaidByText openLogicNodePanel openLogicNodeDocs clearStage
autoNamerTemplate autoNamerSectionTemplate autoNamerDetailsTemplate autoNamerTreeNodeTemplate
autoFillNodeColorToggle autoFillNodeColorSet clickTagPanelButton openOutlineWindow openColorManagerWindow
toggleBackgroundHorizontalLines toggleBackgroundVerticalLines toggleBackgroundDots toggleBackgroundCartesian
switchDebugShow switchStealthMode toggleStealthModeReverseMask stealthModeScopeRadiusIncrease stealthModeScopeRadiusDecrease
exportSelectedNetStructureToPlainText exportSelectedTreeStructureToPlainText exportSelectedTreeStructureToMarkdown
exportSelectedNetStructureToMermaid generateKeyboardLayout openOfficialDocs
watchBilibiliVideo2 watchBilibiliVideo1_6Basic watchBilibiliVideo1_6Advanced watchBilibiliVideo1_0`.split(/\s+/));

type Item = { id: string; type: string; label?: string; visible?: boolean; children?: Item[] };
type Actions = Record<string, () => void | Promise<void>>;
const labels: Record<string, string> = { newPrgAtCurrentDir: '新建.prg', openFile: '导入 .prg',
  openCurrentProjectFileFolder: '资源管理器', saveFile: '保存', saveAs: '另存为副本', manualBackup: '下载 .prg',
  clickAppMenuSettingsButton: '配置', openAppearanceSettings: '主题', nodeDetails: '正文',
  downloadCanvas: '当前视野 PNG', openAboutWindow: 'Project Graph · GPL-3.0', sourceCode: '下载对应源码' };

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
    if (item.visible === false) return [];
    if (item.children) {
      const children = prune(item.children);
      return children.length ? [{ ...item, children }] : [];
    }
    if (item.type === 'separator') return [item];
    return nativeCommands.has(item.id) || actions[item.id] ? [item] : [];
  }).filter((item, index, list) => item.type !== 'separator' ||
    (index > 0 && index < list.length - 1 && list[index - 1].type !== 'separator'));
  const config = prune(Settings.globalMenuConfig as Item[]);
  config.find(item => item.id === 'file')?.children?.push({ type: 'item', id: 'downloadCanvas' });
  config.find(item => item.id === 'window')?.children?.unshift({ type: 'item', id: 'nodeDetails' });
  config.find(item => item.id === 'about')?.children?.push({ type: 'item', id: 'sourceCode' });
  async function resolve(item: Item): Promise<unknown> {
    return { id: item.id, label: labels[item.id] ?? i18next.t(`${item.id}.title`, { ns: 'keyBinds', defaultValue: item.label ?? item.id }),
      separator: item.type === 'separator', disabled: item.type === 'item' && !actions[item.id] && !(await KeyBindsUI.canExecute(item.id, project)),
      children: item.children ? await Promise.all(item.children.map(resolve)) : undefined };
  }
  return Promise.all(config.map(resolve));
}
export async function executeBrowserCommand(id: string, project: Project, actions: Actions) {
  if (!actions[id] && !nativeCommands.has(id)) return;
  project.controller.pressingKeySet.clear();
  if (actions[id]) await actions[id]();
  else await KeyBindsUI.execute(id, project);
  project.controller.resetCountdownTimer();
  project.renderer.tick();
}
