<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import type { FanxiuSubtaskNode, FanxiuSubtaskTreeResponse } from '@/api/fanxiu/scheduler';
import { subtaskStatusLabel, subtaskSummary, subtaskTime } from './subtaskPresentation';

const props = defineProps<{ tree?: FanxiuSubtaskTreeResponse; loading: boolean; error?: string; entryId: string }>();
const emit = defineEmits<{ refresh: [] }>();
const router = useRouter();
// Manual choices survive refresh; settled instances start folded.
const expansion = ref<Record<string, boolean>>({});
const settled = new Set(['completed', 'retained', 'settled', 'expired', 'unavailable', 'not_applicable', 'superseded']);
const isExpanded = (node: FanxiuSubtaskNode) => expansion.value[node.id] ?? !settled.has(node.status);
const selectedId = ref<string | null>(null);
const allNodes = computed(() => {
  const result: FanxiuSubtaskNode[] = [];
  const visit = (nodes: FanxiuSubtaskNode[]) => { for (const node of nodes) { result.push(node); visit(node.children); } };
  visit(props.tree?.nodes || []);
  return result;
});
const selected = computed(() => allNodes.value.find(node => node.id === selectedId.value) || null);
const rows = computed(() => {
  const result: Array<{ node: FanxiuSubtaskNode; depth: number }> = [];
  const visit = (nodes: FanxiuSubtaskNode[], depth: number) => {
    for (const node of nodes) {
      result.push({ node, depth });
      if (isExpanded(node)) visit(node.children, depth + 1);
    }
  };
  visit(props.tree?.nodes || [], 0);
  return result;
});
const timeText = (value?: string | null) => value ? value.replace('T', ' ').replace(/([+-]\d{2}:\d{2}|Z)$/, '') : '—';
const instanceText = (node: FanxiuSubtaskNode) => node.kind === 'instance'
  ? node.message.split('；')[0].replace(/ \d{2}:\d{2}/g, '').replace(' — ', '–') : '';
const showStatus = (node: FanxiuSubtaskNode) => node.kind === 'subtask' || settled.has(node.status) || ['error', 'blocked', 'pending_validation'].includes(node.status);
const openLogs = (node: FanxiuSubtaskNode) => {
  void router.push({ path: '/fanxiu/kernel-scheduler/logs', query: {
    scope: 'job', item_id: node.task_id, title: node.label, subtask_id: node.id,
    ...(props.entryId ? { entry_id: props.entryId } : {}),
  } });
};
</script>

<template>
  <tr v-if="error || (!tree && loading)" class="tree-feedback">
    <td colspan="3"><span :class="{ 'tree-error': error }" role="status">{{ error || '正在读取子任务…' }}</span><button v-if="error" type="button" :disabled="loading" @click="emit('refresh')">重试</button></td>
  </tr>
  <tr v-for="{ node, depth } in rows" :key="node.id" class="subtask-row" :class="{ 'is-running-leaf': node.kind === 'subtask' && node.status === 'running', 'is-group': node.kind !== 'subtask' }">
    <td class="subtask-name-cell" :style="{ '--tree-depth': depth }">
      <div class="tree-name">
        <button v-if="node.children.length" type="button" class="tree-toggle" :aria-expanded="isExpanded(node)" :aria-label="`${isExpanded(node) ? '折叠' : '展开'}${node.label}`" @click="expansion[node.id] = !isExpanded(node)">{{ isExpanded(node) ? '⌄' : '›' }}</button>
        <span v-else class="tree-spacer" />
        <button type="button" class="node-name" @click="selectedId = node.id">{{ node.label }}</button>
        <span v-if="instanceText(node)" class="instance-note" :title="node.message">{{ instanceText(node) }}</span>
      </div>
    </td>
    <td><button v-if="showStatus(node)" type="button" class="node-status" :class="`status-${node.status}`" :title="node.message || subtaskStatusLabel(node.status)" @click="node.kind === 'subtask' ? openLogs(node) : selectedId = node.id"><span v-if="node.status === 'running'" class="live-dot" /><span v-else-if="node.status === 'completed'" class="complete-mark">✓</span>{{ subtaskStatusLabel(node.status) }}</button></td>
    <td class="subtask-time" :title="timeText(node.retry_at || node.due_at)">{{ node.kind === 'subtask' ? subtaskTime(node.retry_at || node.due_at, tree?.business_date) : '' }}</td>
  </tr>
  <tr v-if="tree && !rows.length && !loading" class="tree-feedback"><td colspan="3">{{ tree.message || '暂无子任务' }}</td></tr>
  <Teleport to="body">
    <el-drawer :model-value="selected !== null" :title="selected?.label || '子任务详情'" size="min(420px, 100vw)" @close="selectedId = null">
      <template v-if="selected">
        <dl class="node-details">
          <dt>业务状态</dt><dd>{{ subtaskStatusLabel(selected.status) }}</dd>
          <dt v-if="selected.cycle_key">业务周期</dt><dd v-if="selected.cycle_key">{{ selected.cycle_key }}</dd>
          <dt v-if="selected.due_at">计划触发</dt><dd v-if="selected.due_at">{{ timeText(selected.due_at) }}</dd>
          <dt v-if="selected.retry_at">下次重试</dt><dd v-if="selected.retry_at">{{ timeText(selected.retry_at) }}</dd>
          <dt v-if="selected.deadline_at">截止时间</dt><dd v-if="selected.deadline_at">{{ timeText(selected.deadline_at) }}</dd>
          <dt v-if="selected.completed_at">完成时间</dt><dd v-if="selected.completed_at">{{ timeText(selected.completed_at) }}</dd>
          <dt>说明</dt><dd>{{ selected.message || '—' }}</dd>
          <dt v-if="selected.kind !== 'subtask'">内部进度</dt><dd v-if="selected.kind !== 'subtask'">{{ subtaskSummary(selected.counts) }}</dd>
          <dt v-if="tree?.fact_captured_at">事实采集</dt><dd v-if="tree?.fact_captured_at">{{ timeText(tree.fact_captured_at) }}</dd>
        </dl>
        <el-button v-if="selected.kind === 'subtask'" @click="openLogs(selected)">查看子任务日志</el-button>
        <el-button :loading="loading" @click="emit('refresh')">刷新</el-button>
      </template>
    </el-drawer>
  </Teleport>
</template>

<style scoped>
.subtask-row td { height: 40px; padding: 0 16px; border-bottom: 1px solid #f1f3f6; font-size: 13px; box-sizing: border-box; }
.subtask-row .subtask-name-cell { padding-left: calc(44px + var(--tree-depth) * 26px); position: relative; }
.tree-name { display: flex; align-items: center; gap: 8px; position: relative; min-width: 0; }
.tree-name::before { content: ''; position: absolute; left: -14px; width: 10px; border-top: 1px solid #dfe5ed; }
.subtask-name-cell::before { content: ''; position: absolute; left: calc(30px + var(--tree-depth) * 26px); top: 0; bottom: 0; border-left: 1px solid #dfe5ed; }
.tree-toggle, .node-name, .node-status, .tree-feedback button { border: 0; background: transparent; cursor: pointer; font: inherit; padding: 0; }
.tree-toggle, .tree-spacer { flex: 0 0 16px; width: 16px; color: #64748b; font-size: 18px; text-align: center; }
.node-name { color: #334155; white-space: nowrap; text-align: left; }
.node-name:hover { color: var(--el-color-primary); }
.is-group .node-name { font-weight: 600; }
.instance-note { color: #94a3b8; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.node-status { display: inline-flex; align-items: center; gap: 7px; color: #94a3b8; white-space: nowrap; }
.node-status:hover { text-decoration: underline; }
.status-running, .status-due { color: var(--el-color-primary); }
.status-error, .status-blocked { color: var(--el-color-danger); }
.status-retry_wait, .status-pending_validation { color: var(--el-color-warning); }
.complete-mark { color: var(--el-color-success); }
.live-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.is-running-leaf { background: #f4f8ff; }
.is-running-leaf .subtask-name-cell { box-shadow: inset 2px 0 var(--el-color-primary); }
.subtask-time { color: #64748b; font-variant-numeric: tabular-nums; }
.tree-feedback td { padding: 10px 44px; color: #94a3b8; font-size: 12px; }
.tree-feedback button { margin-left: 12px; color: var(--el-color-primary); }
.tree-error { color: var(--el-color-danger); }
.node-details { font-size: 14px; margin-bottom: 24px; }
.node-details dt { color: var(--el-text-color-secondary); margin-top: 16px; }
.node-details dd { margin: 5px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
