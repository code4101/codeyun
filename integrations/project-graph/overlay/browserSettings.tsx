import { useState, useSyncExternalStore } from 'react';
import { macInputDevice, setMacInputDevice, subscribeMacInputDevice, type MacInputDevice } from './macInputDevice';
import { sensitivityDefaults, sensitivityRange, sensitivitySnapshot, setSensitivity, subscribeSensitivity, type SensitivityKey } from './cameraSensitivity';
import { bodyPreviewEnabled, setBodyPreviewEnabled, subscribeBodyPreview } from './bodyPreviewSettings';
import { useTranslation } from 'react-i18next';
import { SettingField } from '@/components/ui/field';
import { Settings } from '@/core/service/Settings';
import { Project } from '@/core/Project';
import { TabWorkspace } from '@/core/TabWorkspace';
import { store, tabsAtom } from '@/state';

// Only settings consumed by the browser canvas. Storage, native windows and
// OS integrations remain owned by the host, not editable through desktop forms.
const groupHints: Record<string, string> = {
  '触摸板与视野': '缩放过快请降低缩放灵敏度，滚动过慢请提高滚动灵敏度（作用于滚轮与触摸板，1 为上游原值）；视角移动加速度还会影响 WASD 移动速度。设置即时生效，保存在当前浏览器。',
};
const groups: Record<string, (keyof Settings)[]> = {
  '鼠标与操作': ['mouseRightDragBackground', 'enableSpaceKeyMouseLeftDrag', 'enableDragAutoAlign', 'mouseWheelMode', 'mouseWheelModeReverse', 'mouseWheelWithShiftMode', 'mouseWheelWithCtrlMode', 'mouseWheelWithAltMode', 'enableDragEdgeRotateStructure', 'enableCtrlWheelRotateStructure'],
  '画布与网格': ['showQuickSettingsToolbar', 'showBackgroundHorizontalLines', 'showBackgroundVerticalLines', 'showBackgroundDots', 'showBackgroundCartesian', 'isRenderCenterPointer', 'centerCrosshairShape', 'centerCrosshairAlpha'],
  '触摸板与视野': ['macTrackpadScaleSensitivity', 'moveAmplitude'],
  '节点与连线': ['forceHideTextNodeBorder', 'textNodeInitBorderStyle', 'lineStyle', 'showTreeDirectionHint', 'hideArrowWhenPointingToConnectPoint', 'enableTagTextNodesBigDisplay'],
  '分组': ['sectionBitTitleRenderType', 'sectionBackgroundFillMode', 'sectionInitBorderStyle', 'autoEnterSectionEditMode', 'sectionBigTitleThresholdRatio', 'sectionBigTitleCameraScaleThreshold', 'sectionBigTitleOpacity'],
  '自动命名': ['autoNamerTemplate', 'autoNamerSectionTemplate', 'autoNamerDetailsTemplate', 'autoNamerTreeNodeTemplate'],
};
const macDeviceLabels: Record<MacInputDevice, string> = { auto: '自动识别', trackpad: '触摸板', mouse: '鼠标' };
function MacInputDeviceField() {
  const device = useSyncExternalStore(subscribeMacInputDevice, macInputDevice);
  return <label style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '12px 0' }}>
    Mac 输入设备
    <select aria-label="Mac 输入设备" value={device} onChange={event => setMacInputDevice(event.target.value as MacInputDevice)}>
      {(Object.keys(macDeviceLabels) as MacInputDevice[]).map(key => <option key={key} value={key}>{macDeviceLabels[key]}</option>)}
    </select>
    <span style={{ fontSize: 13, opacity: 0.7 }}>自动识别不准确时请选择实际设备。</span>
  </label>;
}
const sensitivityLabels: Record<SensitivityKey, string> = { scroll: '滚动灵敏度', zoom: '缩放灵敏度' };
function SensitivityField({ name }: { name: SensitivityKey }) {
  const value = useSyncExternalStore(subscribeSensitivity, sensitivitySnapshot)[name];
  const { min, max, step } = sensitivityRange[name];
  return <label style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '12px 0' }}>
    {sensitivityLabels[name]}
    <input type="range" aria-label={sensitivityLabels[name]} min={min} max={max} step={step} value={value} onChange={event => setSensitivity(name, Number(event.target.value))} />
    <span style={{ minWidth: 40 }}>{value.toFixed(2)}×</span>
    <button type="button" disabled={value === sensitivityDefaults[name]} onClick={() => setSensitivity(name, null)}>恢复默认</button>
  </label>;
}
function BrowserSettings() {
  const [search, setSearch] = useState('');
  const previewEnabled = useSyncExternalStore(subscribeBodyPreview, bodyPreviewEnabled);
  const { t } = useTranslation('settings');
  return <div style={{ height: '100%', overflow: 'auto', padding: 24 }}>
    <h1 style={{ fontSize: 20, marginBottom: 16 }}>配置</h1>
    <input aria-label="搜索 PG 配置" placeholder="搜索配置" value={search} onChange={event => setSearch(event.target.value)} style={{ width: '100%', padding: 8, border: '1px solid var(--border)', borderRadius: 4, marginBottom: 16 }} />
    {'节点正文 显示节点正文预览 悬浮 缩略正文'.includes(search.trim()) && <section>
      <h2 style={{ fontSize: 16, margin: '20px 0 8px' }}>节点正文</h2>
      <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <input type="checkbox" checked={previewEnabled} onChange={event => setBodyPreviewEnabled(event.target.checked)} />
        显示节点正文预览
      </label>
      <p style={{ fontSize: 13, opacity: 0.7 }}>默认关闭。开启后悬浮显示缩略正文；也可在画布快捷设置中选择始终显示。此设置适用于本浏览器所有图文件，正文侧栏不受影响。</p>
    </section>}
    {Object.entries(groups).map(([title, keys]) => {
      const matching = keys.filter(key => `${title} ${t(`${key}.title`)} ${t(`${key}.description`, { defaultValue: '' })}`.toLowerCase().includes(search.trim().toLowerCase()));
      const macDevice = title === '触摸板与视野' && `${title} 缩放灵敏度 滚动灵敏度 mac 输入设备 自动识别 触摸板 鼠标`.includes(search.trim().toLowerCase());
      return matching.length || macDevice ? <section key={title}><h2 style={{ fontSize: 16, margin: '20px 0 8px' }}>{title}</h2>{groupHints[title] && <p style={{ fontSize: 13, opacity: 0.7 }}>{groupHints[title]}</p>}{macDevice && <><SensitivityField name="zoom" /><SensitivityField name="scroll" /><MacInputDeviceField /></>}{matching.map(key => <SettingField key={key} settingKey={key} />)}</section> : null;
    })}
  </div>;
}
export function openBrowserSettings(project: Project) {
  const existing = store.get(tabsAtom).find(tab => !tab.closing && tab.title === '配置');
  if (existing) { TabWorkspace.focus(existing.id); return; }
  TabWorkspace.create({ title: '配置', children: <BrowserSettings />, layout: 'docked', contextResourceTab: project, closeOnEscape: false });
}
