<template>
  <div class="pdf-outline-panel" @keydown.stop @contextmenu="handlePanelContextMenu">
    <div v-if="error" class="outline-error" role="alert">
      <span class="outline-error-text">{{ error }}</span>
      <button type="button" class="outline-action" @click="emit('reload')">重新加载</button>
    </div>

    <div v-if="localEntries.length === 0" class="outline-empty">{{ busy ? '目录加载中' : '暂无目录' }}</div>

    <div v-else ref="listRef" class="outline-list" role="tree" aria-label="PDF 目录">
      <div
        v-for="row in visibleRows"
        :key="row.node.id"
        class="outline-row"
        :class="{
          'is-active': row.node.id === activeId,
          'is-dragging': draggingId === row.node.id,
          'is-drop-before': dropTargetId === row.node.id && dropPosition === 'before',
          'is-drop-after': dropTargetId === row.node.id && dropPosition === 'after',
          'is-drop-inside': dropTargetId === row.node.id && dropPosition === 'inside',
        }"
        role="treeitem"
        :aria-level="row.depth + 1"
        :aria-expanded="row.hasChildren ? (row.expanded ? 'true' : 'false') : undefined"
        :aria-selected="row.node.id === activeId ? 'true' : 'false'"
        tabindex="0"
        :draggable="canMutate && !editingId"
        :style="{ paddingLeft: `${6 + row.depth * 14}px` }"
        @click="titleClick(row)"
        @dblclick.stop="toggleRowOnDoubleClick(row)"
        @keydown="handleRowKeydown($event, row)"
        @contextmenu="handleRowContextMenu($event, row)"
        @dragstart="handleDragStart($event, row)"
        @dragover="handleDragOver($event, row)"
        @dragleave="handleDragLeave($event, row)"
        @drop="handleDrop($event, row)"
        @dragend="handleDragEnd"
      >
        <button
          v-if="row.hasChildren"
          type="button"
          class="outline-toggle"
          :aria-label="row.expanded ? '折叠' : '展开'"
          @click.stop="toggleExpand(row.node)"
          @dblclick.stop
        >
          <svg class="outline-caret" :class="{ 'is-open': row.expanded }" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M6 4 L10 8 L6 12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
        <span v-else class="outline-toggle-placeholder" aria-hidden="true"></span>
        <input v-if="editingId === row.node.id" v-model="editingTitle" class="outline-title-input" maxlength="300" aria-label="目录标题"
          @click.stop @dblclick.stop @mousedown.stop @keydown.stop
          @keydown.enter="!$event.isComposing && finishRename()" @keydown.esc="editingId = null" @blur="finishRename" />
        <span v-else class="outline-title" :title="row.node.title" @click.stop="titleClick(row)" @dblclick.stop="toggleRowOnDoubleClick(row)">{{ row.node.title }}</span>
        <span v-if="row.node.page != null" class="outline-page">{{ row.node.page }}</span>

      </div>
    </div>

    <Teleport to="body">
      <div
        v-if="menuOpen"
        ref="menuRef"
        class="outline-context-menu"
        role="menu"
        aria-label="目录操作"
        :style="{ left: `${menuLeft}px`, top: `${menuTop}px` }"
        @keydown.stop="handleMenuKeydown"
        @contextmenu.prevent
      >
        <button
          v-for="(item, index) in menuItems"
          :key="item.key"
          type="button"
          role="menuitem"
          class="outline-context-item"
          :class="{ 'is-danger': item.danger }"
          :disabled="item.disabled"
          :tabindex="index === activeMenuIndex ? 0 : -1"
          @mouseenter="activeMenuIndex = index"
          @click="runMenuAction(item.key)"
        >{{ item.label }}</button>
      </div>
      <div
        v-if="dragHint"
        class="outline-drop-hint"
        :style="{ left: `${hintX}px`, top: `${hintY}px` }"
      >{{ dragHint }}</div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

interface OutlineEntry {
  id: string
  title: string
  page: number | null
  level: number
}

interface OutlineNode {
  id: string
  title: string
  page: number | null
  level: number
  parent: OutlineNode | null
  children: OutlineNode[]
}

interface VisibleRow {
  node: OutlineNode
  depth: number
  hasChildren: boolean
  expanded: boolean
}

type DropPosition = 'before' | 'after' | 'inside'

interface MenuItem {
  key: string
  label: string
  disabled: boolean
  danger?: boolean
}

const props = withDefaults(defineProps<{
  entries: OutlineEntry[]
  currentPage: number
  pageCount: number
  canEdit: boolean
  busy: boolean
  error: string
  canEmbed: boolean
}>(), {
  entries: () => [],
  currentPage: 1,
  pageCount: 0,
  canEdit: false,
  busy: false,
  error: '',
  canEmbed: false,
})

const emit = defineEmits<{
  (e: 'navigate', page: number): void
  (e: 'change', entries: OutlineEntry[]): void
  (e: 'embed'): void
  (e: 'reload'): void
  (e: 'search-section', id: string): void
}>()

function cloneEntry(entry: OutlineEntry): OutlineEntry {
  return { id: entry.id, title: entry.title, page: entry.page, level: entry.level }
}

function cloneEntries(entries: OutlineEntry[]): OutlineEntry[] {
  return entries.map(cloneEntry)
}

function sameEntries(a: OutlineEntry[], b: OutlineEntry[]): boolean {
  if (a === b) return true
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i += 1) {
    const x = a[i]
    const y = b[i]
    if (x.id !== y.id || x.title !== y.title || x.page !== y.page || x.level !== y.level) {
      return false
    }
  }
  return true
}

function createId(): string {
  const cryptoObj = globalThis.crypto as Crypto | undefined
  if (cryptoObj && typeof cryptoObj.randomUUID === 'function') {
    return cryptoObj.randomUUID()
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const random = (Math.random() * 16) | 0
    const value = char === 'x' ? random : (random & 0x3) | 0x8
    return value.toString(16)
  })
}

function isCancel(error: unknown): boolean {
  return error === 'cancel' || error === 'close'
}

const localEntries = ref<OutlineEntry[]>(cloneEntries(props.entries))
const expandedIds = ref<Set<string>>(new Set())
const expansionInitialized = ref(false)

const canMutate = computed(() => props.canEdit && !props.busy)

function buildForest(entries: OutlineEntry[]): { roots: OutlineNode[]; map: Map<string, OutlineNode> } {
  const roots: OutlineNode[] = []
  const map = new Map<string, OutlineNode>()
  const stack: OutlineNode[] = []
  let prevLevel = -1
  for (const entry of entries) {
    let level = Number.isInteger(entry.level) ? entry.level : 0
    if (level < 0) level = 0
    if (level > prevLevel + 1) level = prevLevel + 1
    const node: OutlineNode = {
      id: entry.id,
      title: entry.title ?? '',
      page: entry.page ?? null,
      level,
      parent: null,
      children: [],
    }
    while (stack.length > 0 && stack[stack.length - 1].level >= level) {
      stack.pop()
    }
    const parent = stack.length > 0 ? stack[stack.length - 1] : null
    node.parent = parent
    if (parent) parent.children.push(node)
    else roots.push(node)
    map.set(node.id, node)
    stack.push(node)
    prevLevel = level
  }
  return { roots, map }
}

function flattenForest(roots: OutlineNode[]): OutlineEntry[] {
  const result: OutlineEntry[] = []
  const visit = (nodes: OutlineNode[], depth: number) => {
    for (const node of nodes) {
      result.push({ id: node.id, title: node.title, page: node.page, level: depth })
      if (node.children.length > 0) visit(node.children, depth + 1)
    }
  }
  visit(roots, 0)
  return result
}

const forest = computed(() => buildForest(localEntries.value))

function findActiveEntry(entries: OutlineEntry[], page: number): OutlineEntry | null {
  let best: OutlineEntry | null = null
  for (const entry of entries) {
    if (entry.page == null || entry.page > page) continue
    if (
      !best
      || best.page == null
      || entry.page > best.page
      || (entry.page === best.page && entry.level >= best.level)
    ) {
      best = entry
    }
  }
  return best
}

const activeId = computed(() => findActiveEntry(localEntries.value, props.currentPage)?.id ?? '')
const activeNode = computed(() => (activeId.value ? forest.value.map.get(activeId.value) ?? null : null))

function ancestorIds(node: OutlineNode): string[] {
  const ids: string[] = []
  let current: OutlineNode | null = node
  while (current) {
    ids.push(current.id)
    current = current.parent
  }
  return ids
}

const visibleRows = computed<VisibleRow[]>(() => {
  const expanded = expandedIds.value
  const rows: VisibleRow[] = []
  const visit = (nodes: OutlineNode[], depth: number) => {
    for (const node of nodes) {
      const hasChildren = node.children.length > 0
      const isExpanded = expanded.has(node.id)
      rows.push({ node, depth, hasChildren, expanded: isExpanded })
      if (hasChildren && isExpanded) visit(node.children, depth + 1)
    }
  }
  visit(forest.value.roots, 0)
  return rows
})

const collapsibleIds = computed(() => {
  const ids: string[] = []
  for (const node of forest.value.map.values()) {
    if (node.children.length > 0) ids.push(node.id)
  }
  return ids
})

const allExpanded = computed(() => {
  const ids = collapsibleIds.value
  if (ids.length === 0) return false
  const expanded = expandedIds.value
  return ids.every((id) => expanded.has(id))
})

function ensureInitialExpansion() {
  if (expansionInitialized.value) return
  if (localEntries.value.length === 0) return
  const node = activeNode.value
  const next = new Set<string>()
  if (node) {
    for (const id of ancestorIds(node)) {
      const current = forest.value.map.get(id)
      if (current && current.children.length > 0) next.add(id)
    }
  }
  expandedIds.value = next
  expansionInitialized.value = true
}

watch(() => props.entries, (next) => {
  if (sameEntries(next, localEntries.value)) return
  localEntries.value = cloneEntries(next)
  expandedIds.value = new Set()
  expansionInitialized.value = false
  ensureInitialExpansion()
}, { deep: true })

watch(() => props.currentPage, () => {
  ensureInitialExpansion()
  const node = activeNode.value
  if (!node) return
  const next = new Set(expandedIds.value)
  for (const id of ancestorIds(node)) {
    const current = forest.value.map.get(id)
    if (current && current.children.length > 0) next.add(id)
  }
  expandedIds.value = next
})

function toggleExpand(node: OutlineNode) {
  if (node.children.length === 0) return
  const next = new Set(expandedIds.value)
  if (next.has(node.id)) next.delete(node.id)
  else next.add(node.id)
  expandedIds.value = next
}

function toggleExpandAll() {
  if (allExpanded.value) expandedIds.value = new Set()
  else expandedIds.value = new Set(collapsibleIds.value)
}

function clampPage(value: number): number {
  const upper = Math.max(props.pageCount || 1, 1)
  const numeric = Math.floor(Number(value) || 1)
  return Math.min(Math.max(numeric, 1), upper)
}

function subtreeEnd(entries: OutlineEntry[], index: number): number {
  const level = entries[index].level
  let end = index + 1
  while (end < entries.length && entries[end].level > level) end += 1
  return end
}

function commit(flat: OutlineEntry[]) {
  if (flat.some(entry => entry.level > 20)) { ElMessage.warning('目录层级最多 21 层'); return }
  localEntries.value = flat
  emit('change', cloneEntries(flat))
}

function withForest(mutator: (map: Map<string, OutlineNode>, roots: OutlineNode[]) => boolean | void) {
  const { roots, map } = buildForest(localEntries.value)
  const proceed = mutator(map, roots)
  if (proceed === false) return
  commit(flattenForest(roots))
}

function siblingArray(node: OutlineNode, roots: OutlineNode[]): OutlineNode[] {
  return node.parent ? node.parent.children : roots
}

function insertAt(index: number, entry: OutlineEntry) {
  const flat = localEntries.value.map(cloneEntry)
  flat.splice(index, 0, entry)
  commit(flat)
}

async function addEntry(node: OutlineNode | null, child = false) {
  if (!canMutate.value) return
  const page = clampPage(props.currentPage)
  try {
    const result = await ElMessageBox.prompt(`链接到第 ${page} 页`, child ? '添加子级目录' : '添加目录', {
      inputValue: '', confirmButtonText: '添加', cancelButtonText: '取消',
      inputValidator: (value: string) => Boolean(value?.trim()) && value.trim().length <= 300 || '请输入 1–300 字的标题',
    })
    if (!canMutate.value) return
    const entries = localEntries.value
    const index = node ? entries.findIndex(item => item.id === node.id) : -1
    if (node && index < 0) return
    insertAt(index < 0 ? entries.length : subtreeEnd(entries, index), {
      id: createId(), title: result.value.trim(), page,
      level: index < 0 ? 0 : entries[index].level + (child ? 1 : 0),
    })
    if (node && child) expandedIds.value = new Set([...expandedIds.value, node.id])
  } catch (error) {
    if (!isCancel(error)) ElMessage.error('添加目录失败')
  }
}

function addCurrentPage() { void addEntry(null) }
function addSibling(node: OutlineNode) { void addEntry(node) }
function addChild(node: OutlineNode) { void addEntry(node, true) }

function setCurrentPage(node: OutlineNode) {
  if (!canMutate.value) return
  const page = clampPage(props.currentPage)
  withForest((map) => {
    const target = map.get(node.id)
    if (target) target.page = page
  })
}

const editingId = ref<string | null>(null)
const editingTitle = ref('')
let titleClickTimer: ReturnType<typeof setTimeout> | undefined

function titleClick(row: VisibleRow) {
  clearTimeout(titleClickTimer)
  titleClickTimer = setTimeout(() => handleRowClick(row), 250)
}

function toggleRowOnDoubleClick(row: VisibleRow) {
  clearTimeout(titleClickTimer)
  if (row.hasChildren) toggleExpand(row.node)
}

async function renameNode(node: OutlineNode) {
  clearTimeout(titleClickTimer)
  if (!canMutate.value) return
  closeMenu()
  editingId.value = node.id
  editingTitle.value = node.title
  await nextTick()
  const input = listRef.value?.querySelector<HTMLInputElement>('.outline-title-input')
  input?.focus()
  input?.select()
}

function finishRename() {
  const id = editingId.value
  if (!id) return
  const title = editingTitle.value.trim()
  editingId.value = null
  if (!title || !canMutate.value || forest.value.map.get(id)?.title === title) return
  withForest(map => {
    const target = map.get(id)
    if (target) target.title = title
  })
}

async function changePage(node: OutlineNode) {
  if (!canMutate.value) return
  const upper = Math.max(props.pageCount || 1, 1)
  try {
    const result = await ElMessageBox.prompt(`页码范围 1 - ${upper}，留空表示无页码`, '修改页码', {
      inputValue: node.page == null ? '' : String(node.page),
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      inputValidator: (value: string) => {
        const text = String(value ?? '').trim()
        if (text === '') return true
        const numeric = Number(text)
        if (!Number.isInteger(numeric) || numeric < 1 || numeric > upper) {
          return `请输入 1 - ${upper} 之间的整数`
        }
        return true
      },
    })
    const text = String(result.value ?? '').trim()
    const page = text === '' ? null : Number(text)
    withForest((map) => {
      const target = map.get(node.id)
      if (target) target.page = page
    })
  } catch (error) {
    if (isCancel(error)) return
    ElMessage.error('修改页码失败')
  }
}

async function deleteNode(node: OutlineNode) {
  if (!canMutate.value) return
  const entries = localEntries.value
  const index = entries.findIndex((item) => item.id === node.id)
  const childCount = index < 0 ? 0 : subtreeEnd(entries, index) - index - 1
  try {
    await ElMessageBox.confirm(
      childCount > 0
        ? `确定删除「${node.title}」及其 ${childCount} 个子项？`
        : `确定删除「${node.title}」？`,
      '删除目录',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
      },
    )
  } catch {
    return
  }
  withForest((map, roots) => {
    const target = map.get(node.id)
    if (!target) return false
    const siblings = siblingArray(target, roots)
    const position = siblings.indexOf(target)
    if (position >= 0) siblings.splice(position, 1)
  })
  ElMessage.success('已删除')
}

function moveUp(id: string) {
  if (!canMutate.value) return
  withForest((map, roots) => {
    const node = map.get(id)
    if (!node) return false
    const siblings = siblingArray(node, roots)
    const index = siblings.indexOf(node)
    if (index <= 0) return false
    siblings.splice(index, 1)
    siblings.splice(index - 1, 0, node)
  })
}

function moveDown(id: string) {
  if (!canMutate.value) return
  withForest((map, roots) => {
    const node = map.get(id)
    if (!node) return false
    const siblings = siblingArray(node, roots)
    const index = siblings.indexOf(node)
    if (index < 0 || index >= siblings.length - 1) return false
    siblings.splice(index, 1)
    siblings.splice(index + 1, 0, node)
  })
}

function outdent(id: string) {
  if (!canMutate.value) return
  withForest((map, roots) => {
    const node = map.get(id)
    if (!node || !node.parent) return false
    const parent = node.parent
    const parentSiblings = siblingArray(parent, roots)
    const parentIndex = parentSiblings.indexOf(parent)
    const siblings = parent.children
    const index = siblings.indexOf(node)
    if (index < 0 || parentIndex < 0) return false
    siblings.splice(index, 1)
    parentSiblings.splice(parentIndex + 1, 0, node)
    node.parent = parent.parent
  })
}

function indent(id: string) {
  if (!canMutate.value) return
  withForest((map, roots) => {
    const node = map.get(id)
    if (!node) return false
    const siblings = siblingArray(node, roots)
    const index = siblings.indexOf(node)
    if (index <= 0) return false
    const previous = siblings[index - 1]
    siblings.splice(index, 1)
    previous.children.push(node)
    node.parent = previous
  })
}

function isDescendant(candidateId: string, ancestorId: string): boolean {
  const entries = localEntries.value
  const ancestorIndex = entries.findIndex((item) => item.id === ancestorId)
  const candidateIndex = entries.findIndex((item) => item.id === candidateId)
  if (ancestorIndex < 0 || candidateIndex < 0) return false
  return candidateIndex > ancestorIndex && candidateIndex < subtreeEnd(entries, ancestorIndex)
}

function moveSubtree(dragId: string, targetId: string, position: DropPosition) {
  if (dragId === targetId) return
  if (isDescendant(targetId, dragId)) return
  withForest((map, roots) => {
    const drag = map.get(dragId)
    const target = map.get(targetId)
    if (!drag || !target) return false
    const dragSiblings = siblingArray(drag, roots)
    const dragIndex = dragSiblings.indexOf(drag)
    if (dragIndex >= 0) dragSiblings.splice(dragIndex, 1)
    if (position === 'inside') {
      target.children.push(drag)
      drag.parent = target
      return
    }
    const targetSiblings = siblingArray(target, roots)
    const targetIndex = targetSiblings.indexOf(target)
    const insertIndex = position === 'before' ? targetIndex : targetIndex + 1
    targetSiblings.splice(insertIndex, 0, drag)
    drag.parent = target.parent
  })
}

const menuOpen = ref(false)
const menuNodeId = ref<string | null>(null)
const menuX = ref(0)
const menuY = ref(0)
const menuLeft = ref(0)
const menuTop = ref(0)
const activeMenuIndex = ref(0)
const menuRef = ref<HTMLElement | null>(null)
const listRef = ref<HTMLElement | null>(null)
let menuTriggerEl: HTMLElement | null = null

const menuNode = computed(() => (menuNodeId.value ? forest.value.map.get(menuNodeId.value) ?? null : null))

const menuItems = computed<MenuItem[]>(() => {
  const node = menuNode.value
  if (!node) return [
    { key: 'expand-all', label: '全部展开', disabled: !collapsibleIds.value.length },
    { key: 'collapse-all', label: '全部折叠', disabled: !expandedIds.value.size },
    ...(props.canEdit ? [{ key: 'add-current', label: '添加当前页', disabled: !canMutate.value }] : []),
  ]
  const locked = !canMutate.value
  if (!props.canEdit) return [{key:'search-section', label:'搜索该节', disabled:false}]
  return [
    { key: 'search-section', label: '搜索该节', disabled: false },
    { key: 'add-current', label: '添加当前页', disabled: locked },
    { key: 'rename', label: '重命名', disabled: locked },
    { key: 'set-current', label: '设为当前页', disabled: locked },
    { key: 'delete', label: '删除', disabled: locked, danger: true },
  ]
})

function positionMenu() {
  const element = menuRef.value
  if (!element) return
  const padding = 8
  const width = element.offsetWidth
  const height = element.offsetHeight
  menuLeft.value = Math.max(padding, Math.min(menuX.value, window.innerWidth - width - padding))
  menuTop.value = Math.max(padding, Math.min(menuY.value, window.innerHeight - height - padding))
}

function menuButtons(): HTMLButtonElement[] {
  if (!menuRef.value) return []
  return Array.from(menuRef.value.querySelectorAll<HTMLButtonElement>('button.outline-context-item'))
}

function focusMenuItem(index: number) {
  const buttons = menuButtons().filter((button) => !button.disabled)
  if (buttons.length === 0) return
  const next = ((index % buttons.length) + buttons.length) % buttons.length
  activeMenuIndex.value = next
  buttons[next].focus()
}

function removeMenuListeners() {
  window.removeEventListener('mousedown', onDocumentMouseDown, true)
  window.removeEventListener('scroll', onDocumentScroll, true)
  window.removeEventListener('resize', onDocumentScroll)
  window.removeEventListener('keydown', onDocumentKeydown, true)
}

function closeMenu(refocus = false) {
  if (!menuOpen.value) return
  menuOpen.value = false
  menuNodeId.value = null
  removeMenuListeners()
  if (refocus && menuTriggerEl) {
    const trigger = menuTriggerEl
    void nextTick(() => trigger.focus())
  }
  menuTriggerEl = null
}

function openMenu(nodeId: string | null, x: number, y: number, trigger: HTMLElement | null) {
  menuNodeId.value = nodeId
  menuX.value = x
  menuY.value = y
  menuLeft.value = x
  menuTop.value = y
  menuTriggerEl = trigger
  menuOpen.value = true
  activeMenuIndex.value = 0
  window.addEventListener('mousedown', onDocumentMouseDown, true)
  window.addEventListener('scroll', onDocumentScroll, true)
  window.addEventListener('resize', onDocumentScroll)
  window.addEventListener('keydown', onDocumentKeydown, true)
  void nextTick(() => {
    positionMenu()
    focusMenuItem(0)
  })
}

function onDocumentMouseDown(event: MouseEvent) {
  const target = event.target
  if (menuRef.value && target instanceof Node && menuRef.value.contains(target)) return
  // The trigger may be the entire panel; only clicks inside the menu stay open.
  closeMenu()
}

function onDocumentScroll() {
  closeMenu()
}

function onDocumentKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeMenu(true)
  }
}

function handleMenuKeydown(event: KeyboardEvent) {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    focusMenuItem(activeMenuIndex.value + 1)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    focusMenuItem(activeMenuIndex.value - 1)
  } else if (event.key === 'Home') {
    event.preventDefault()
    focusMenuItem(0)
  } else if (event.key === 'End') {
    event.preventDefault()
    focusMenuItem(menuButtons().length - 1)
  } else if (event.key === 'Tab') {
    closeMenu()
  }
}

function openMenuFromRow(node: OutlineNode, trigger: HTMLElement) {
  const rect = trigger.getBoundingClientRect()
  openMenu(node.id, rect.right - 8, rect.bottom + 2, trigger)
}

async function runMenuAction(key: string) {
  if (key === 'expand-all' || key === 'collapse-all') {
    closeMenu()
    expandedIds.value = key === 'expand-all' ? new Set(collapsibleIds.value) : new Set()
    return
  }
  if (key === 'search-section' && menuNode.value) { const id = menuNode.value.id; closeMenu(); emit('search-section', id); return }
  if (key === 'add-current') { closeMenu(); addCurrentPage(); return }
  const node = menuNode.value
  if (!node) {
    closeMenu()
    return
  }
  closeMenu()
  switch (key) {
    case 'rename':
      await renameNode(node)
      break
    case 'set-current':
      setCurrentPage(node)
      break
    case 'delete':
      await deleteNode(node)
      break
  }
}

const draggingId = ref<string | null>(null)
const dropTargetId = ref<string | null>(null)
const dropPosition = ref<DropPosition | null>(null)
const dragHint = ref('')
const hintX = ref(0)
const hintY = ref(0)

function clearDrop() {
  dropTargetId.value = null
  dropPosition.value = null
  dragHint.value = ''
}

function handleDragStart(event: DragEvent, row: VisibleRow) {
  if (!canMutate.value) {
    event.preventDefault()
    return
  }
  draggingId.value = row.node.id
  clearDrop()
  if (event.dataTransfer) {
    event.dataTransfer.setData('text/plain', row.node.id)
    event.dataTransfer.effectAllowed = 'move'
  }
}

function handleDragOver(event: DragEvent, row: VisibleRow) {
  const dragId = draggingId.value
  if (!canMutate.value || !dragId) return
  if (row.node.id === dragId || isDescendant(row.node.id, dragId)) {
    clearDrop()
    return
  }
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  const ratio = rect.height > 0 ? (event.clientY - rect.top) / rect.height : 0.5
  const position: DropPosition = ratio < 1 / 3 ? 'before' : ratio > 2 / 3 ? 'after' : 'inside'
  dropTargetId.value = row.node.id
  dropPosition.value = position
  dragHint.value = position === 'inside'
    ? `作为「${row.node.title}」的子级`
    : `插入到「${row.node.title}」${position === 'before' ? '之前' : '之后'}`
  hintX.value = event.clientX + 14
  hintY.value = event.clientY + 16
}

function handleDragLeave(event: DragEvent, row: VisibleRow) {
  const related = event.relatedTarget
  if (related instanceof Node && (event.currentTarget as HTMLElement).contains(related)) return
  if (dropTargetId.value === row.node.id) clearDrop()
}

function handleDrop(event: DragEvent, row: VisibleRow) {
  event.preventDefault()
  const dragId = draggingId.value
  const position = dropPosition.value
  if (dragId && position && dropTargetId.value === row.node.id) {
    moveSubtree(dragId, row.node.id, position)
  }
  handleDragEnd()
}

function handleDragEnd() {
  draggingId.value = null
  clearDrop()
}

function handleRowClick(row: VisibleRow) {
  if (row.node.page != null) emit('navigate', row.node.page)
  else if (row.hasChildren) toggleExpand(row.node)
}

function handlePanelContextMenu(event: MouseEvent) {
  event.preventDefault()
  openMenu(null, event.clientX, event.clientY, event.currentTarget as HTMLElement)
}
function handleRowContextMenu(event: MouseEvent, row: VisibleRow) {
  event.stopPropagation()
  event.preventDefault()
  const trigger = event.currentTarget as HTMLElement
  if (event.clientX === 0 && event.clientY === 0) openMenuFromRow(row.node, trigger)
  else openMenu(row.node.id, event.clientX, event.clientY, trigger)
}

function focusRow(trigger: HTMLElement, direction: number) {
  const list = listRef.value
  if (!list) return
  const rows = Array.from(list.querySelectorAll<HTMLElement>('.outline-row'))
  const index = rows.indexOf(trigger)
  const next = rows[index + direction]
  if (next) next.focus()
}

function handleRowKeydown(event: KeyboardEvent, row: VisibleRow) {
  if (event.key === 'F10' && event.shiftKey) {
    if (!props.canEdit) return
    event.preventDefault()
    openMenuFromRow(row.node, event.currentTarget as HTMLElement)
    return
  }
  if (event.key === 'ContextMenu') {
    if (!props.canEdit) return
    event.preventDefault()
    openMenuFromRow(row.node, event.currentTarget as HTMLElement)
    return
  }
  if (event.altKey && props.canEdit) {
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      moveUp(row.node.id)
    } else if (event.key === 'ArrowDown') {
      event.preventDefault()
      moveDown(row.node.id)
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault()
      outdent(row.node.id)
    } else if (event.key === 'ArrowRight') {
      event.preventDefault()
      indent(row.node.id)
    }
    return
  }
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    focusRow(event.currentTarget as HTMLElement, 1)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    focusRow(event.currentTarget as HTMLElement, -1)
  } else if (event.key === 'ArrowRight') {
    if (row.hasChildren && !row.expanded) {
      event.preventDefault()
      toggleExpand(row.node)
    }
  } else if (event.key === 'ArrowLeft') {
    if (row.hasChildren && row.expanded) {
      event.preventDefault()
      toggleExpand(row.node)
    }
  } else if (event.key === 'Enter') {
    event.preventDefault()
    handleRowClick(row)
  }
}

onBeforeUnmount(() => {
  clearTimeout(titleClickTimer)
  removeMenuListeners()
  menuOpen.value = false
})

ensureInitialExpansion()
</script>

<style scoped>
.outline-title-input { width: 100%; min-width: 0; box-sizing: border-box; padding: 2px 4px; border: 1px solid #409eff; border-radius: 3px; font: inherit; color: inherit; outline: none; }
.pdf-outline-panel {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  color: #1f2937;
  font-size: 13px;
}

.outline-action {
  padding: 4px 6px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #2563eb;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
}

.outline-action:hover {
  background: #eff6ff;
}

.outline-action:disabled {
  background: transparent;
  color: #cbd5e1;
  cursor: default;
}

.outline-action.is-primary {
  background: #2563eb;
  color: #fff;
}

.outline-action.is-primary:hover:not(:disabled) {
  background: #1d4ed8;
}

.outline-action.is-primary:disabled {
  background: #bfdbfe;
  color: #fff;
}

.outline-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 6px 8px;
  padding: 6px 8px;
  border-radius: 6px;
  background: #fef2f2;
  color: #b91c1c;
  font-size: 12px;
}

.outline-error-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.outline-empty {
  padding: 18px 8px;
  color: #64748b;
  font-size: 13px;
  text-align: center;
}

.outline-list {
  flex: 1;
  min-height: 0;
  padding: 4px;
  overflow: auto;
}

.outline-row {
  box-sizing: border-box;
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr) auto;
  align-items: center;
  gap: 4px;
  width: 100%;
  min-height: 30px;
  padding-right: 4px;
  border-radius: 6px;
  color: #334155;
  outline: none;
}

.outline-row:hover {
  background: #f1f5f9;
}

.outline-row:focus-visible {
  box-shadow: inset 0 0 0 2px #93c5fd;
}

.outline-row.is-active {
  background: #e8f2ff;
  color: #1d4ed8;
}

.outline-row.is-dragging {
  opacity: 0.5;
}

.outline-row.is-drop-before {
  box-shadow: inset 0 2px 0 0 #2563eb;
}

.outline-row.is-drop-after {
  box-shadow: inset 0 -2px 0 0 #2563eb;
}

.outline-row.is-drop-inside {
  background: #e0edff;
  box-shadow: inset 0 0 0 1px #2563eb;
}

.outline-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  font: inherit;
}

.outline-toggle:hover {
  background: rgba(37, 99, 235, 0.1);
  color: #2563eb;
}

.outline-toggle-placeholder {
  display: inline-block;
  width: 24px;
  height: 28px;
}

.outline-caret {
  display: block;
  width: 12px;
  height: 12px;
  opacity: .65;
  transition: transform 0.15s ease;
}

.outline-caret.is-open {
  transform: rotate(90deg);
}

.outline-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.outline-page {
  color: #64748b;
  font-size: 12px;
}

.outline-context-menu {
  position: fixed;
  z-index: 3000;
  display: flex;
  flex-direction: column;
  min-width: 132px;
  padding: 4px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.16);
}

.outline-context-item {
  display: block;
  width: 100%;
  padding: 6px 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #334155;
  cursor: pointer;
  font: inherit;
  font-size: 13px;
  text-align: left;
}

.outline-context-item:hover:not(:disabled),
.outline-context-item:focus-visible {
  background: #eff6ff;
  color: #1d4ed8;
  outline: none;
}

.outline-context-item:disabled {
  color: #cbd5e1;
  cursor: default;
}

.outline-context-item.is-danger {
  color: #dc2626;
}

.outline-context-item.is-danger:hover:not(:disabled) {
  background: #fef2f2;
  color: #b91c1c;
}

.outline-drop-hint {
  position: fixed;
  z-index: 3001;
  padding: 3px 8px;
  border-radius: 6px;
  background: rgba(37, 99, 235, 0.92);
  color: #fff;
  font-size: 12px;
  white-space: nowrap;
  pointer-events: none;
}
</style>



