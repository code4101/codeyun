import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Menubar, MenubarMenu, MenubarTrigger, MenubarContent, MenubarItem, MenubarSeparator, MenubarSub, MenubarSubTrigger, MenubarSubContent } from '@/components/ui/menubar';
import { Settings } from '@/core/service/Settings';
import { KeyBindsUI } from '@/core/service/controlService/shortcutKeysEngine/KeyBindsUI';
import { allKeyBinds } from '@/core/service/controlService/shortcutKeysEngine/shortcutKeysRegister';
import { Project } from '@/core/Project';
import { toast } from 'sonner';

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
  clickAppMenuSettingsButton: '配置', openAppearanceSettings: '主题', nodeDetails: '节点正文',
  downloadCanvas: '当前视野 PNG', openAboutWindow: 'Project Graph · GPL-3.0', sourceCode: '下载对应源码' };

export function installBrowserCommands(actions: Actions) {
  for (const [id, action] of Object.entries(actions)) {
    const previous = KeyBindsUI.getUIKeyBind(id);
    KeyBindsUI.registerOneUIKeyBind(id, previous?.key ?? '', true,
      () => { void Promise.resolve().then(action).catch(error => toast.error(String(error))); },
      undefined, false, () => true, previous?.icon);
  }
}

export default function BrowserMenu({ getProject, actions }: { getProject: () => Project | undefined; actions: Actions }) {
  const { t } = useTranslation('keyBinds');
  const [enabled, setEnabled] = useState<Record<string, boolean>>({});
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
  const refresh = async () => {
    const entries = await Promise.all([...nativeCommands].map(async id => [id, await KeyBindsUI.canExecute(id, getProject())] as const));
    setEnabled(Object.fromEntries(entries));
  };
  const execute = async (id: string) => {
    try {
      const project = getProject();
      if (!project) return;
      project.controller.pressingKeySet.clear();
      if (actions[id]) await actions[id]();
      else await KeyBindsUI.execute(id, project);
      project.controller.resetCountdownTimer();
      project.renderer.tick();
    } catch (error) { toast.error(String(error)); }
  };
  const render = (item: Item): ReactNode => {
    if (item.type === 'separator') return <MenubarSeparator key={item.id} />;
    const Icon = allKeyBinds.find(command => command.id === item.id)?.icon;
    const title = labels[item.id] ?? t(`${item.id}.title`, item.label ?? item.id);
    if (item.children) return <MenubarSub key={item.id}><MenubarSubTrigger>{title}</MenubarSubTrigger><MenubarSubContent>{item.children.map(render)}</MenubarSubContent></MenubarSub>;
    return <MenubarItem key={item.id} disabled={!actions[item.id] && enabled[item.id] !== true} onSelect={() => void execute(item.id)}>{Icon && <Icon />}{title}</MenubarItem>;
  };
  return <Menubar className="codeyun-menubar" onValueChange={() => void refresh()} aria-label="绘图菜单">
    {config.map(menu => <MenubarMenu key={menu.id}><MenubarTrigger>{t(`${menu.id}.title`, menu.id)}</MenubarTrigger><MenubarContent>{menu.children?.map(render)}</MenubarContent></MenubarMenu>)}
  </Menubar>;
}
