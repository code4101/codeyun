import type { DockTool } from '@/components/docking/dockLayout'

const settingsTool: DockTool = { id: 'settings', title: '配置', icon: 'settings', position: 'left' }

/** 业务只声明工具与默认编组，移动、显隐、分割和持久化由通用停靠层负责。 */
export const bookDockTools: readonly DockTool[] = [
  { id: 'toc', title: '目录', icon: 'document', position: 'left', open: true },
  { id: 'search', title: '搜索全书', icon: 'search', position: 'left' },
  { id: 'outline', title: '大纲', icon: 'outline', position: 'right', open: true },
  settingsTool,
]
export const skillDockTools: readonly DockTool[] = [
  { id: 'toc', title: '目录', icon: 'document', position: 'left', open: true },
  { id: 'outline', title: '大纲', icon: 'outline', position: 'right', open: true },
  settingsTool,
]
export const pdfDockTools: readonly DockTool[] = [
  { id: 'toc', title: '目录', icon: 'document', position: 'left', open: true },
  { id: 'search', title: '搜索全书', icon: 'search', position: 'left' },
  { id: 'info', title: '信息', icon: 'info', position: 'left' },
  { id: 'ocr', title: '全书 OCR', icon: 'ocr', position: 'left' },
  { id: 'outline', title: '大纲', icon: 'outline', position: 'right', open: true },
  settingsTool,
]
