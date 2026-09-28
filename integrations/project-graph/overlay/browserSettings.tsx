import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { SettingField } from '@/components/ui/field';
import { Settings } from '@/core/service/Settings';
import { Project } from '@/core/Project';
import { TabWorkspace } from '@/core/TabWorkspace';
import { store, tabsAtom } from '@/state';

// Only settings consumed by the browser canvas. Storage, native windows and
// OS integrations remain owned by the host, not editable through desktop forms.
const groups: Record<string, (keyof Settings)[]> = {
  '鼠标与操作': ['mouseRightDragBackground', 'enableSpaceKeyMouseLeftDrag', 'enableDragAutoAlign', 'mouseWheelMode', 'mouseWheelModeReverse', 'mouseWheelWithShiftMode', 'mouseWheelWithCtrlMode', 'mouseWheelWithAltMode', 'enableDragEdgeRotateStructure', 'enableCtrlWheelRotateStructure'],
  '画布与网格': ['showQuickSettingsToolbar', 'showBackgroundHorizontalLines', 'showBackgroundVerticalLines', 'showBackgroundDots', 'showBackgroundCartesian', 'isRenderCenterPointer', 'centerCrosshairShape', 'centerCrosshairAlpha'],
  '节点与连线': ['forceHideTextNodeBorder', 'textNodeInitBorderStyle', 'lineStyle', 'showTreeDirectionHint', 'hideArrowWhenPointingToConnectPoint', 'enableTagTextNodesBigDisplay'],
  '分组': ['sectionBitTitleRenderType', 'sectionBackgroundFillMode', 'sectionInitBorderStyle', 'autoEnterSectionEditMode', 'sectionBigTitleThresholdRatio', 'sectionBigTitleCameraScaleThreshold', 'sectionBigTitleOpacity'],
  '自动命名': ['autoNamerTemplate', 'autoNamerSectionTemplate', 'autoNamerDetailsTemplate', 'autoNamerTreeNodeTemplate'],
};
function BrowserSettings() {
  const [search, setSearch] = useState('');
  const { t } = useTranslation('settings');
  return <div style={{ height: '100%', overflow: 'auto', padding: 24 }}>
    <h1 style={{ fontSize: 20, marginBottom: 16 }}>配置</h1>
    <input aria-label="搜索 PG 配置" placeholder="搜索配置" value={search} onChange={event => setSearch(event.target.value)} style={{ width: '100%', padding: 8, border: '1px solid var(--border)', borderRadius: 4, marginBottom: 16 }} />
    {Object.entries(groups).map(([title, keys]) => {
      const matching = keys.filter(key => `${title} ${t(`${key}.title`)} ${t(`${key}.description`, { defaultValue: '' })}`.toLowerCase().includes(search.trim().toLowerCase()));
      return matching.length ? <section key={title}><h2 style={{ fontSize: 16, margin: '20px 0 8px' }}>{title}</h2>{matching.map(key => <SettingField key={key} settingKey={key} />)}</section> : null;
    })}
  </div>;
}
export function openBrowserSettings(project: Project) {
  const existing = store.get(tabsAtom).find(tab => !tab.closing && tab.title === '配置');
  if (existing) { TabWorkspace.focus(existing.id); return; }
  TabWorkspace.create({ title: '配置', children: <BrowserSettings />, layout: 'docked', contextResourceTab: project, closeOnEscape: false });
}
