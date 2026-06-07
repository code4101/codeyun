<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import NoteEditor from '@/components/NoteEditor.vue'

type TaskStatus = 'open' | 'done'

type TaskNode = {
  id: number
  title: string
  content: string
  parentId: number | null
  sortOrder: number
  status: TaskStatus
  createdAt: string
  updatedAt: string
}

type TaskTreeNode = TaskNode & {
  children: TaskTreeNode[]
}

const STORAGE_KEY = 'codeyun.notes.taskSystem.v1'

const tasks = ref<TaskNode[]>([])
const selectedId = ref<number | null>(null)
const expandedIds = ref<Set<number>>(new Set())

const canUseLocalStorage = () =>
  typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'

const nowIso = () => new Date().toISOString()

const formatTime = (value: string) => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const year = date.getFullYear()
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  const hour = `${date.getHours()}`.padStart(2, '0')
  const minute = `${date.getMinutes()}`.padStart(2, '0')
  return `${year}-${month}-${day} ${hour}:${minute}`
}

const orderedTasks = computed(() =>
  [...tasks.value].sort((left, right) => {
    if (left.parentId !== right.parentId) {
      return (left.parentId ?? 0) - (right.parentId ?? 0)
    }
    if (left.sortOrder !== right.sortOrder) return left.sortOrder - right.sortOrder
    return left.id - right.id
  }),
)

const taskById = computed(() => new Map(tasks.value.map((task) => [task.id, task])))

const selectedTask = computed(() =>
  selectedId.value == null ? null : (taskById.value.get(selectedId.value) ?? null),
)

const nextTaskNumber = computed(() =>
  tasks.value.reduce((current, task) => Math.max(current, task.id), 0) + 1,
)

const childCountByParent = computed(() => {
  const counts = new Map<number | null, number>()
  for (const task of tasks.value) {
    counts.set(task.parentId, (counts.get(task.parentId) ?? 0) + 1)
  }
  return counts
})

const taskTree = computed<TaskTreeNode[]>(() => {
  const children = new Map<number | null, TaskNode[]>()
  for (const task of orderedTasks.value) {
    const group = children.get(task.parentId) ?? []
    group.push(task)
    children.set(task.parentId, group)
  }
  const build = (parentId: number | null): TaskTreeNode[] =>
    (children.get(parentId) ?? []).map((task) => ({
      ...task,
      children: build(task.id),
    }))
  return build(null)
})

const expandedTaskKeys = computed(() => [...expandedIds.value])

const taskTreeProps = {
  label: 'title',
  children: 'children',
}

const loadTasks = () => {
  if (!canUseLocalStorage()) return
  const raw = window.localStorage.getItem(STORAGE_KEY)
  if (!raw) return
  try {
    const parsed = JSON.parse(raw) as TaskNode[]
    if (!Array.isArray(parsed)) return
    tasks.value = parsed
      .filter((task) => Number.isInteger(task.id) && typeof task.title === 'string')
      .map((task) => ({
        id: task.id,
        title: task.title,
        content: typeof task.content === 'string' ? task.content : '',
        parentId: Number.isInteger(task.parentId) ? task.parentId : null,
        sortOrder: Number.isFinite(task.sortOrder) ? task.sortOrder : task.id,
        status: task.status === 'done' ? 'done' : 'open',
        createdAt: task.createdAt || nowIso(),
        updatedAt: task.updatedAt || nowIso(),
      }))
  } catch {
    tasks.value = []
  }
}

const saveTasks = () => {
  if (!canUseLocalStorage()) return
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks.value))
}

const selectTask = (id: number) => {
  selectedId.value = id
}

const setTaskExpanded = (id: number, expanded: boolean) => {
  const next = new Set(expandedIds.value)
  if (expanded) next.add(id)
  else next.delete(id)
  expandedIds.value = next
}

const createTask = (parentId: number | null = null) => {
  const timestamp = nowIso()
  const task: TaskNode = {
    id: nextTaskNumber.value,
    title: '',
    content: '',
    parentId,
    sortOrder: childCountByParent.value.get(parentId) ?? 0,
    status: 'open',
    createdAt: timestamp,
    updatedAt: timestamp,
  }
  tasks.value = [...tasks.value, task]
  if (parentId != null) {
    expandedIds.value = new Set(expandedIds.value).add(parentId)
  }
  selectedId.value = task.id
}

const updateSelectedTask = (patch: Partial<Pick<TaskNode, 'title' | 'content' | 'status'>>) => {
  const current = selectedTask.value
  if (!current) return
  tasks.value = tasks.value.map((task) =>
    task.id === current.id
      ? {
          ...task,
          ...patch,
          updatedAt: nowIso(),
        }
      : task,
  )
}

const collectDescendantIds = (id: number) => {
  const ids = new Set<number>([id])
  let changed = true
  while (changed) {
    changed = false
    for (const task of tasks.value) {
      if (task.parentId != null && ids.has(task.parentId) && !ids.has(task.id)) {
        ids.add(task.id)
        changed = true
      }
    }
  }
  return ids
}

const isTaskDescendantOf = (taskId: number, possibleAncestorId: number) => {
  let current = taskById.value.get(taskId)
  while (current?.parentId != null) {
    if (current.parentId === possibleAncestorId) return true
    current = taskById.value.get(current.parentId)
  }
  return false
}

const allowTaskDrop = (
  draggingNode: { data?: TaskTreeNode },
  dropNode: { data?: TaskTreeNode },
  type: 'prev' | 'inner' | 'next',
) => {
  const dragging = draggingNode.data
  const target = dropNode.data
  if (!dragging || !target || dragging.id === target.id) return false
  if (type === 'inner') return !isTaskDescendantOf(target.id, dragging.id)
  if (target.parentId != null && (target.parentId === dragging.id || isTaskDescendantOf(target.parentId, dragging.id))) {
    return false
  }
  return true
}

const normalizeSiblingOrders = (parentId: number | null, orderedIds: number[]) => {
  const orderById = new Map(orderedIds.map((id, index) => [id, index]))
  tasks.value = tasks.value.map((task) => (
    task.parentId === parentId && orderById.has(task.id)
      ? { ...task, sortOrder: orderById.get(task.id) ?? task.sortOrder, updatedAt: nowIso() }
      : task
  ))
}

const moveTaskToParent = (taskId: number, parentId: number | null, orderedIds: number[]) => {
  const orderById = new Map(orderedIds.map((id, index) => [id, index]))
  const timestamp = nowIso()
  tasks.value = tasks.value.map((task) => {
    if (task.id === taskId) {
      return {
        ...task,
        parentId,
        sortOrder: orderById.get(task.id) ?? orderedIds.length - 1,
        updatedAt: timestamp,
      }
    }
    if (task.parentId === parentId && orderById.has(task.id)) {
      return {
        ...task,
        sortOrder: orderById.get(task.id) ?? task.sortOrder,
        updatedAt: timestamp,
      }
    }
    return task
  })
}

const handleTaskDrop = (
  draggingNode: { data?: TaskTreeNode },
  dropNode: { data?: TaskTreeNode },
  type: 'before' | 'after' | 'inner',
) => {
  const dragging = draggingNode.data
  const target = dropNode.data
  if (!dragging || !target || dragging.id === target.id) return

  if (type === 'inner') {
    if (isTaskDescendantOf(target.id, dragging.id)) return
    const parentId = target.id
    const orderedIds = orderedTasks.value
      .filter((task) => task.parentId === parentId && task.id !== dragging.id)
      .map((task) => task.id)
    orderedIds.push(dragging.id)
    moveTaskToParent(dragging.id, parentId, orderedIds)
    setTaskExpanded(parentId, true)
    selectedId.value = dragging.id
    return
  }

  const parentId = target.parentId
  if (parentId != null && (parentId === dragging.id || isTaskDescendantOf(parentId, dragging.id))) return
  const orderedIds = orderedTasks.value
    .filter((task) => task.parentId === parentId && task.id !== dragging.id)
    .map((task) => task.id)
  const targetIndex = orderedIds.indexOf(target.id)
  const insertIndex = Math.max(0, type === 'before' ? targetIndex : targetIndex + 1)
  orderedIds.splice(insertIndex, 0, dragging.id)

  if (dragging.parentId === parentId) {
    normalizeSiblingOrders(parentId, orderedIds)
  } else {
    moveTaskToParent(dragging.id, parentId, orderedIds)
  }
  selectedId.value = dragging.id
}

const removeSelectedTask = async () => {
  const current = selectedTask.value
  if (!current) return
  const ids = collectDescendantIds(current.id)
  const count = ids.size
  try {
    await ElMessageBox.confirm(
      count > 1 ? `删除 #${current.id} 及其 ${count - 1} 个子任务？` : `删除 #${current.id}？`,
      '删除任务',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
      },
    )
    tasks.value = tasks.value.filter((task) => !ids.has(task.id))
    expandedIds.value = new Set([...expandedIds.value].filter((id) => !ids.has(id)))
    selectedId.value = orderedTasks.value[0]?.id ?? null
  } catch {
    // 用户取消删除。
  }
}

onMounted(() => {
  loadTasks()
  selectedId.value = tasks.value[0]?.id ?? null
  expandedIds.value = new Set(tasks.value.filter((task) => task.parentId == null).map((task) => task.id))
})

watch(tasks, saveTasks, { deep: true })
</script>

<template>
  <main class="task-system-page">
    <header class="task-system-header">
      <h1>任务系统</h1>
    </header>

    <section class="task-workspace">
      <aside class="task-tree-panel">
        <div class="panel-title">
          <span>任务树</span>
          <div class="panel-title-actions">
            <button
              class="root-add-button"
              type="button"
              title="在最下面创建一级 task"
              aria-label="在最下面创建一级 task"
              @click="createTask()"
            >
              +
            </button>
          </div>
        </div>
        <div v-if="taskTree.length" class="task-tree">
          <el-tree
            class="task-el-tree"
            :data="taskTree"
            :props="taskTreeProps"
            node-key="id"
            :default-expanded-keys="expandedTaskKeys"
            :auto-expand-parent="false"
            highlight-current
            draggable
            :expand-on-click-node="false"
            :current-node-key="selectedId"
            :allow-drop="allowTaskDrop"
            @node-click="(node: TaskTreeNode) => selectTask(node.id)"
            @node-expand="(node: TaskTreeNode) => setTaskExpanded(node.id, true)"
            @node-collapse="(node: TaskTreeNode) => setTaskExpanded(node.id, false)"
            @node-drop="handleTaskDrop"
          >
            <template #default="{ data }">
              <span class="task-tree-node" :class="{ done: data.status === 'done' }">
                <span class="task-number">#{{ data.id }}</span>
                <span class="task-title" :class="{ untitled: !data.title.trim() }">
                  {{ data.title.trim() || '未命名 task' }}
                </span>
                <span v-if="data.status === 'done'" class="done-mark">完成</span>
                <button
                  class="task-node-add-button"
                  type="button"
                  title="创建子 task"
                  aria-label="创建子 task"
                  @mousedown.stop
                  @click.stop="createTask(data.id)"
                >
                  +
                </button>
              </span>
            </template>
          </el-tree>
        </div>
        <el-empty v-else description="还没有 task" />
      </aside>

      <section class="task-detail-panel">
        <template v-if="selectedTask">
          <div class="detail-toolbar">
            <div class="detail-title">
              <span class="detail-number">#{{ selectedTask.id }}</span>
              <el-input
                :model-value="selectedTask.title"
                class="title-input"
                placeholder="任务标题"
                @update:model-value="updateSelectedTask({ title: $event.trimStart() })"
              />
            </div>
            <div class="detail-actions">
              <button
                class="detail-icon-button remove-task-button"
                type="button"
                title="删除 task"
                aria-label="删除 task"
                @click="removeSelectedTask"
              >
                -
              </button>
            </div>
          </div>

          <div class="meta-row">
            <span>创建：{{ formatTime(selectedTask.createdAt) }}</span>
            <span>更新：{{ formatTime(selectedTask.updatedAt) }}</span>
          </div>

          <NoteEditor
            :model-value="selectedTask.content"
            layout="flow"
            :min-height="430"
            :show-wrap-toggle="true"
            @update:model-value="updateSelectedTask({ content: $event })"
          />
        </template>
        <el-empty v-else description="选择一个 task 查看详情" />
      </section>
    </section>
  </main>
</template>

<style scoped>
.task-system-page {
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - 58px);
  padding: 18px 20px;
  background: #f6f8fb;
}

.task-system-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.task-system-header h1 {
  margin: 0;
  color: #1f2937;
  font-size: 22px;
  font-weight: 650;
}

.task-workspace {
  display: grid;
  grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
  gap: 14px;
  min-height: 0;
  flex: 1;
}

.task-tree-panel,
.task-detail-panel {
  min-width: 0;
  overflow: hidden;
  background: #fff;
  border: 1px solid #e5e7eb;
}

.task-tree-panel {
  display: flex;
  flex-direction: column;
}

.panel-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  height: 44px;
  padding: 0 14px;
  border-bottom: 1px solid #edf0f3;
  color: #111827;
  font-weight: 600;
}

.panel-title-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.root-add-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  color: #fff;
  font-size: 18px;
  line-height: 1;
  background: #22c55e;
  border: 0;
  border-radius: 50%;
  cursor: pointer;
}

.root-add-button:hover {
  background: #16a34a;
}

.task-tree {
  overflow: auto;
  padding: 6px 0;
}

.task-el-tree {
  --el-tree-node-hover-bg-color: #f5f7fb;
  --el-tree-node-content-height: 34px;
  color: #1f2937;
  background: transparent;
}

.task-el-tree :deep(.el-tree-node__content) {
  min-width: 0;
}

.task-el-tree :deep(.el-tree-node__content > .task-tree-node) {
  flex: 1;
}

.task-el-tree :deep(.el-tree-node.is-current > .el-tree-node__content) {
  background: #eaf3ff;
}

.task-el-tree :deep(.el-tree-node__expand-icon) {
  color: #6b7280;
}

.task-el-tree :deep(.el-tree-node__content:hover .task-tree-node) {
  color: #111827;
}

.task-el-tree :deep(.el-tree-node.is-drop-inner > .el-tree-node__content) {
  background: #dcfce7;
}

.task-tree-node {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  max-width: 100%;
  padding-right: 8px;
}

.task-tree-node.done .task-title {
  color: #6b7280;
  text-decoration: line-through;
}

.task-number,
.detail-number {
  color: #64748b;
  font-variant-numeric: tabular-nums;
}

.task-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.task-title.untitled {
  color: #9ca3af;
}

.done-mark {
  color: #059669;
  font-size: 12px;
}

.task-node-add-button {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  color: #16a34a;
  font-size: 16px;
  line-height: 1;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  cursor: pointer;
  opacity: 0.35;
}

.task-el-tree :deep(.el-tree-node__content:hover .task-node-add-button),
.task-el-tree :deep(.el-tree-node.is-current > .el-tree-node__content .task-node-add-button),
.task-node-add-button:focus-visible {
  opacity: 1;
}

.task-node-add-button:hover {
  border-color: #86efac;
  background: #f0fdf4;
}

.task-detail-panel {
  display: flex;
  flex-direction: column;
  padding: 16px;
}

.detail-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}

.detail-title {
  display: grid;
  grid-template-columns: max-content minmax(180px, 520px);
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.title-input {
  min-width: 0;
}

.detail-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.detail-icon-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  padding: 0;
  font-size: 22px;
  line-height: 1;
  background: #fff;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  cursor: pointer;
}

.remove-task-button {
  color: #dc2626;
}

.remove-task-button:hover {
  color: #b91c1c;
  border-color: #fca5a5;
  background: #fef2f2;
}

.meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 18px;
  margin: 12px 0;
  color: #6b7280;
  font-size: 13px;
}

@media (max-width: 900px) {
  .task-system-page {
    padding: 14px;
  }

  .task-workspace {
    grid-template-columns: 1fr;
  }

  .detail-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .detail-title {
    grid-template-columns: max-content minmax(0, 1fr);
  }

  .detail-actions {
    justify-content: flex-start;
  }
}
</style>
