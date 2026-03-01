import { type NoteNode } from '@/api/notes';

/**
 * 节点类型视觉配置项接口
 */
export interface NodeConfigItem {
  id: string;
  label: string;
  description: string;
  borderColor: string;
  backgroundColor: string;
  color: string;
  borderWidth: string;
  borderStyle: string;
  fontWeight: string;
  textDecoration: string;
  opacity: string;
  buttonType: string;
}

/**
 * 节点类型视觉配置
 */
export const NODE_TYPE_CONFIG: Record<string, NodeConfigItem> = {
  project: {
    id: 'project',
    label: '项目 (Project)',
    description: '长期性工作，非具体任务容器。',
    borderColor: '#9c27b0',
    backgroundColor: '#f3e5f5',
    color: '#7b1fa2',
    borderWidth: '3px',
    borderStyle: 'solid',
    fontWeight: 'bold',
    textDecoration: 'none',
    opacity: '1',
    buttonType: 'primary'
  },
  module: {
    id: 'module',
    label: '模块 (Module)',
    description: '项目的组成部分，中间层级。',
    borderColor: '#ba68c8',
    backgroundColor: '#faf4fb',
    color: '#9c27b0',
    borderWidth: '2px',
    borderStyle: 'solid',
    fontWeight: 'bold',
    textDecoration: 'none',
    opacity: '1',
    buttonType: 'primary'
  },
  todo: {
    id: 'todo',
    label: '待办 (Todo)',
    description: '计划要做的具体事项。',
    borderColor: '#409eff',
    backgroundColor: '#fff',
    color: '#409eff',
    borderWidth: '2px',
    borderStyle: 'solid',
    fontWeight: 'bold',
    textDecoration: 'none',
    opacity: '1',
    buttonType: 'primary'
  },
  doing: {
    id: 'doing',
    label: '进行中 (Doing)',
    description: '正在处理的任务。',
    borderColor: '#e6a23c',
    backgroundColor: '#fdf6ec',
    color: '#e6a23c',
    borderWidth: '2px',
    borderStyle: 'solid',
    fontWeight: 'bold',
    textDecoration: 'none',
    opacity: '1',
    buttonType: 'warning'
  },
  'pre-done': {
    id: 'pre-done',
    label: '预完成 (Pre-done)',
    description: '已初步完成，待验证或确认。',
    borderColor: '#e6e6e6',
    backgroundColor: '#fafafa',
    color: '#909399',
    borderWidth: '2px',
    borderStyle: 'dashed',
    fontWeight: 'normal',
    textDecoration: 'none',
    opacity: '1',
    buttonType: 'info'
  },
  done: {
    id: 'done',
    label: '完成 (Done)',
    description: '任务正式结束。',
    borderColor: '#e6e6e6',
    backgroundColor: '#fafafa',
    color: '#909399',
    borderWidth: '1px',
    borderStyle: 'solid',
    fontWeight: 'normal',
    textDecoration: 'none',
    opacity: '1',
    buttonType: 'info'
  },
  delete: {
    id: 'delete',
    label: '删除/放弃 (Delete)',
    description: '原定计划取消或放弃，加删除线。',
    borderColor: '#dcdfe6',
    backgroundColor: '#f5f7fa',
    color: '#c0c4cc',
    borderWidth: '1px',
    borderStyle: 'solid',
    fontWeight: 'normal',
    textDecoration: 'line-through',
    opacity: '0.6',
    buttonType: 'info'
  },
  bug: {
    id: 'bug',
    label: '缺陷 (Bug)',
    description: '需要修复的问题。',
    borderColor: '#f56c6c',
    backgroundColor: '#fef0f0',
    color: '#f56c6c',
    borderWidth: '2px',
    borderStyle: 'solid',
    fontWeight: 'bold',
    textDecoration: 'none',
    opacity: '1',
    buttonType: 'danger'
  },
  memo: {
    id: 'memo',
    label: '备忘 (Memo)',
    description: '记录信息，非任务。',
    borderColor: '#303133',
    backgroundColor: '#fff',
    color: '#000000',
    borderWidth: '2px',
    borderStyle: 'solid',
    fontWeight: 'bold',
    textDecoration: 'none',
    opacity: '1',
    buttonType: 'success'
  },
  default: {
    id: 'default',
    label: '普通 (None)',
    description: '普通笔记节点。',
    borderColor: '#dcdfe6',
    backgroundColor: '#fff',
    color: '#303133',
    borderWidth: '1px',
    borderStyle: 'solid',
    fontWeight: 'normal',
    textDecoration: 'none',
    opacity: '1',
    buttonType: ''
  }
};

/**
 * 规范的展示顺序
 */
export const NODE_TYPE_ORDER = [
  'project',
  'module',
  'memo',
  'bug',
  'todo',
  'doing',
  'pre-done',
  'done',
  'delete'
];

/**
 * 获取规范顺序的配置列表
 */
export const getOrderedNodeConfigs = () => {
  return NODE_TYPE_ORDER.map(key => NODE_TYPE_CONFIG[key]);
};

/**
 * 获取节点的视觉配置
 */
export const getNodeConfig = (type: string | null | undefined) => {
  return NODE_TYPE_CONFIG[type || 'default'] || NODE_TYPE_CONFIG.default;
};
