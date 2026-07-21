<template>
  <div class="access-node">
    <div class="node-row">
      <div class="node-main" :style="{ paddingLeft: `${depth * 18}px` }">
        <button
          v-if="hasChildren"
          class="node-toggle"
          type="button"
          :aria-label="collapsed ? `展开${item.title}` : `收起${item.title}`"
          :aria-expanded="!collapsed"
          :title="collapsed ? '展开目录' : '收起目录'"
          @click="emit('toggle-collapse', item.key)"
        >
          <el-icon><ArrowRight /></el-icon>
        </button>
        <span v-else class="node-toggle-placeholder" aria-hidden="true" />
        <el-checkbox
          :model-value="item.effective_value"
          :disabled="disabled"
          @change="handleEffectiveToggle"
        />
        <span class="node-title">{{ item.title }}</span>
        <el-tag size="small" effect="plain" :type="item.node_type === 'group' ? 'info' : 'success'">
          {{ item.node_type === 'group' ? '目录' : '页面' }}
        </el-tag>
        <el-tag size="small" effect="plain" :type="item.effective_value ? 'success' : 'info'">
          {{ item.effective_value ? '当前允许' : '当前禁止' }}
        </el-tag>
        <span class="node-reason">{{ sourceLabel }}</span>
      </div>

      <div class="node-actions">
        <el-radio-group
          size="small"
          :model-value="item.local_decision"
          :disabled="disabled"
          @change="handleDecisionChange"
        >
          <el-radio-button value="inherit">{{ inheritLabel }}</el-radio-button>
          <el-radio-button value="allow">{{ allowLabel }}</el-radio-button>
          <el-radio-button value="deny">{{ denyLabel }}</el-radio-button>
        </el-radio-group>
      </div>
    </div>

    <div v-if="hasChildren" v-show="!collapsed" class="node-children">
      <FeatureAccessTreeNode
        v-for="child in item.children"
        :key="child.key"
        :item="child"
        :depth="depth + 1"
        :subject-kind="subjectKind"
        :disabled="disabled"
        :collapsed-keys="collapsedKeys"
        @change-decision="forwardDecisionChange"
        @toggle-collapse="forwardToggleCollapse"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ArrowRight } from '@element-plus/icons-vue'
import { computed } from 'vue'

import type { FeatureAccessDecision, FeatureAccessTreeItem } from '@/api/access'

defineOptions({
  name: 'FeatureAccessTreeNode',
})

const props = defineProps<{
  item: FeatureAccessTreeItem
  depth: number
  subjectKind: 'anonymous' | 'user'
  disabled?: boolean
  collapsedKeys: ReadonlySet<string>
}>()

const emit = defineEmits<{
  (event: 'change-decision', key: string, decision: FeatureAccessDecision): void
  (event: 'toggle-collapse', key: string): void
}>()

const hasChildren = computed(() => props.item.children.length > 0)
const collapsed = computed(() => props.collapsedKeys.has(props.item.key))

const inheritLabel = computed(() => (
  props.subjectKind === 'anonymous' ? '默认' : '继承游客'
))

const allowLabel = computed(() => (
  props.subjectKind === 'anonymous' ? '开放' : '允许'
))

const denyLabel = computed(() => (
  props.subjectKind === 'anonymous' ? '关闭' : '禁止'
))

const sourceLabel = computed(() => {
  if (props.item.source === 'superuser') {
    return '超管恒有权限'
  }
  if (props.item.source === 'ancestor_denied') {
    return '受上级关闭影响'
  }
  if (props.item.local_decision === 'inherit') {
    return props.subjectKind === 'anonymous'
      ? '使用注册表默认'
      : '继承游客'
  }
  if (props.subjectKind === 'anonymous') {
    return props.item.local_decision === 'allow' ? '本节点默认开放' : '本节点默认关闭'
  }
  return props.item.local_decision === 'allow' ? '本节点强制允许' : '本节点强制禁止'
})

const handleDecisionChange = (decision: string | number | boolean) => {
  if (decision !== 'inherit' && decision !== 'allow' && decision !== 'deny') {
    return
  }
  emit('change-decision', props.item.key, decision)
}

const handleEffectiveToggle = (checked: string | number | boolean) => {
  emit('change-decision', props.item.key, checked ? 'allow' : 'deny')
}

const forwardDecisionChange = (key: string, decision: FeatureAccessDecision) => {
  emit('change-decision', key, decision)
}

const forwardToggleCollapse = (key: string) => {
  emit('toggle-collapse', key)
}
</script>

<style scoped>
.access-node {
  display: flex;
  flex-direction: column;
}

.node-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
  min-height: 40px;
  padding: 6px 0;
  border-bottom: 1px solid #f0f2f5;
}

.node-main {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.node-title {
  color: #303133;
  font-size: 14px;
}

.node-toggle,
.node-toggle-placeholder {
  width: 20px;
  height: 20px;
  flex: 0 0 20px;
}

.node-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  color: #606266;
  cursor: pointer;
  background: transparent;
  border: 0;
  border-radius: 4px;
  transition: color 0.15s ease, background-color 0.15s ease;
}

.node-toggle:hover,
.node-toggle:focus-visible {
  color: #409eff;
  background: #ecf5ff;
  outline: none;
}

.node-toggle .el-icon {
  transition: transform 0.15s ease;
}

.node-toggle[aria-expanded='true'] .el-icon {
  transform: rotate(90deg);
}

.node-reason {
  font-size: 12px;
  color: #909399;
}

.node-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
}

.node-children {
  display: flex;
  flex-direction: column;
}

@media (max-width: 1080px) {
  .node-row {
    grid-template-columns: 1fr;
  }

  .node-actions {
    justify-content: flex-start;
    padding-left: 28px;
  }
}
</style>
