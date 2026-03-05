
import { type NoteNode } from '@/api/notes';

/**
 * 节点类型定义
 */
export interface NodeTypeItem {
  id: string;
  label: string; // 中文
  labelEn: string; // 英文
  description: string;
  baseColor: string; // 主色调 (边框、文字)
  lightColor: string; // 浅色背景 (用于 doing/highlight)
}

/**
 * 节点状态定义
 */
export interface NodeStatusItem {
  id: string;
  label: string; // 中文
  labelEn: string; // 英文
  description: string;
}

export const NODE_TYPES: Record<string, NodeTypeItem> = {
  project: {
    id: 'project',
    label: '项目',
    labelEn: 'Project',
    description: '长期性工作，非具体任务容器',
    baseColor: '#7b1fa2',
    lightColor: '#f3e5f5'
  },
  module: {
    id: 'module',
    label: '模块',
    labelEn: 'Module',
    description: '项目的组成部分',
    baseColor: '#ba68c8',
    lightColor: '#faf4fb'
  },
  task: {
    id: 'task',
    label: '任务',
    labelEn: 'Task',
    description: '具体的执行事项',
    baseColor: '#409eff',
    lightColor: '#ecf5ff'
  },
  bug: {
    id: 'bug',
    label: '缺陷',
    labelEn: 'Bug',
    description: '需要修复的问题',
    baseColor: '#f56c6c',
    lightColor: '#fef0f0'
  },
  note: {
    id: 'note',
    label: '笔记',
    labelEn: 'Note',
    description: '一般性记录 (默认)',
    baseColor: '#606266', // Dark gray
    lightColor: '#f4f4f5'
  },
  doc: {
    id: 'doc',
    label: '文档',
    labelEn: 'Doc',
    description: '知识库、文档',
    baseColor: '#26c6da',
    lightColor: '#e0f7fa'
  },
  memo: {
    id: 'memo',
    label: '备忘',
    labelEn: 'Memo',
    description: '临时备忘、便签',
    baseColor: '#e6a23c',
    lightColor: '#fdf6ec'
  }
};

export const NODE_STATUSES: Record<string, NodeStatusItem> = {
  idea: { id: 'idea', label: '想法', labelEn: 'Idea', description: '初始状态' },
  todo: { id: 'todo', label: '待办', labelEn: 'Todo', description: '计划中' },
  doing: { id: 'doing', label: '进行中', labelEn: 'Doing', description: '正在处理' },
  predone: { id: 'predone', label: '预完成', labelEn: 'Pre-done', description: '待验收' },
  done: { id: 'done', label: '完成', labelEn: 'Done', description: '已结束' },
  delete: { id: 'delete', label: '废弃', labelEn: 'Delete', description: '已取消' }
};

export const NODE_TYPE_ORDER = ['project', 'module', 'task', 'bug', 'note', 'doc', 'memo'];
export const NODE_STATUS_ORDER = ['idea', 'todo', 'doing', 'predone', 'done', 'delete'];

/**
 * 计算最终视觉样式
 */
export const getNodeStyle = (typeStr: string | null | undefined, statusStr: string | null | undefined) => {
  const type = NODE_TYPES[typeStr || 'note'] || NODE_TYPES.note;
  const status = NODE_STATUSES[statusStr || 'idea'] || NODE_STATUSES.idea;

  // Default Style (Clean slate)
  let style = {
    borderColor: '#000000', // Default black border for status
    backgroundColor: '#ffffff', // Default no background
    color: type.baseColor, // Font color bound to Type
    borderWidth: '1px',
    borderStyle: 'solid',
    fontWeight: 'normal',
    textDecoration: 'none',
    opacity: '1',
  };

  switch (status.id) {
    case 'idea':
      // No border? Or maybe very light gray?
      // User said "cleanest default style", maybe no border or very subtle.
      // "idea可以无边框？" -> Let's try no border or very light gray border.
      // But nodes usually need some boundary. Let's use a very light gray border.
      style.borderStyle = 'none'; // Or 'solid' with #f0f0f0
      // Actually 'none' might make it hard to see white on white. 
      // Let's use a shadow or just a very subtle border.
      // User said "idea可以无边框？", let's try border: none.
      // Note: If background is white and canvas is white/gray, it might blend in.
      // Let's add a subtle shadow in the component if needed, or just 1px solid #eee.
      // Let's go with 1px solid #ebeef5 (very light gray) to define the area.
      style.borderStyle = 'solid';
      style.borderColor = '#ebeef5'; 
      break;
    case 'todo':
      // Dashed black border
      style.borderStyle = 'dashed';
      style.borderColor = '#606266'; // Dark gray/Black-ish
      break;
    case 'doing':
      // Solid black border
      style.borderStyle = 'solid';
      style.borderColor = '#303133'; // Black
      break;
    case 'predone':
      // Dashed Type-Color border
      style.borderStyle = 'dashed';
      style.borderColor = type.baseColor;
      style.backgroundColor = type.lightColor;
      break;
    case 'done':
      // Solid Type-Color border
      style.borderStyle = 'solid';
      style.borderColor = type.baseColor;
      style.backgroundColor = type.lightColor;
      break;
    case 'delete':
      // No border + Strike-through
      // Idea style (solid very light gray) + Strike-through
      style.borderStyle = 'solid';
      style.borderColor = '#ebeef5';
      style.textDecoration = 'line-through';
      // Do NOT change color, keep type color
      // style.color = '#c0c4cc'; // Removed gray text override
      style.opacity = '0.6';
      break;
  }
  
  // Force background to empty (white) as requested
  // style.backgroundColor = '#ffffff'; // Removed global override to allow done state background

  return style;
};

// Compatibility Helpers
export const getOrderedNodeTypes = () => NODE_TYPE_ORDER.map(k => NODE_TYPES[k]);
export const getOrderedNodeStatuses = () => NODE_STATUS_ORDER.map(k => NODE_STATUSES[k]);

export const getNodeTypeConfig = (type: string) => NODE_TYPES[type] || NODE_TYPES.note;
export const getNodeStatusConfig = (status: string) => NODE_STATUSES[status] || NODE_STATUSES.idea;
