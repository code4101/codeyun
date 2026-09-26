<template>
  <el-dialog
    :model-value="modelValue"
    title="调整全局目录"
    width="min(720px, 94vw)"
    :close-on-click-modal="false"
    :close-on-press-escape="!saving"
    :show-close="!saving"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-alert
      title="拖到任意节点中央成为其子节点，拖到上／下边缘与它同级；拖到父节点边缘可提升一级。"
      description="松手后自动保存，对所有账号生效，并同步侧边栏。移动后权限按新父目录继承。"
      type="info"
      :closable="false"
      show-icon
    />
    <div class="directory-editor-status" role="status">{{ saving ? '正在保存…' : '拖动节点标题调整目录' }}</div>
    <el-tree
      class="directory-editor-tree"
      :data="tree"
      node-key="key"
      :props="{ label: 'title', children: 'children' }"
      draggable
      default-expand-all
      :expand-on-click-node="false"
      :allow-drag="() => !saving"
      :allow-drop="allowDrop"
      @node-drag-start="captureSnapshot"
      @node-drop="handleDrop"
    >
      <template #default="{ data }">
        <span class="directory-editor-label">⠿ {{ data.title }}</span>
        <el-tag size="small" effect="plain" type="info">{{ data.node_type === 'group' || data.children.length ? '目录' : '页面' }}</el-tag>
      </template>
    </el-tree>
    <template #footer>
      <el-button :disabled="saving" @click="emit('update:modelValue', false)">完成</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { RenderContentContext } from 'element-plus'
import type { FeatureAccessTreeItem } from '@/api/access'
import { moveFeatureDirectoryNode } from '@/api/adminFeatureAccess'
import { canDropDirectory } from '@/features/access/directoryDrag'

const props = defineProps<{ modelValue: boolean; items: FeatureAccessTreeItem[] }>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  saved: []
}>()
const saving = ref(false)
const tree = ref<FeatureAccessTreeItem[]>([])
type TreeNode = RenderContentContext['node']
let snapshot: FeatureAccessTreeItem[] = []
const captureSnapshot = () => { snapshot = JSON.parse(JSON.stringify(tree.value)) }

watch(() => props.modelValue, (visible) => {
  if (visible) tree.value = JSON.parse(JSON.stringify(props.items))
})

const allowDrop = (source: TreeNode, target: TreeNode, type: string) => {
  if (saving.value) return false
  return canDropDirectory(source.data as FeatureAccessTreeItem, target.data as FeatureAccessTreeItem, type === 'inner' ? 'inside' : type === 'prev' ? 'before' : 'after')
}

const handleDrop = async (source: TreeNode, target: TreeNode, type: string) => {
  if (type !== 'before' && type !== 'after' && type !== 'inner') return
  // Element Plus moves its local nodes immediately; keep a snapshot for failed writes.
  const previous = snapshot
  saving.value = true
  try {
    await moveFeatureDirectoryNode(source.data.key, target.data.key, type === 'inner' ? 'inside' : type)
    // ElTree already applied this move. Replacing its data here would rebuild
    // nodes, reset expansion, and disturb scrolling after every successful drop.
    emit('saved')
  } catch (error: any) {
    tree.value = previous
    ElMessage.error(error.response?.data?.detail || '保存目录失败')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.directory-editor-status { margin: 12px 0; color: #909399; font-size: 13px; }
.directory-editor-tree { max-height: 60vh; overflow: auto; }
.directory-editor-tree :deep(.el-tree-node__content) { height: 36px; }
.directory-editor-label { margin-right: 8px; cursor: grab; }
</style>
