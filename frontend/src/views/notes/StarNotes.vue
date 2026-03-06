<template>
  <div class="task-manager-layout">
    <div class="filter-section" v-if="isGlobalGraph">
      <NoteProgramBar
        v-model="dataProgram"
        title="后端筛选"
        help-text="决定从后端加载哪些节点，点击“执行”后生效并保存；规则按顺序执行，后面的包含/排除可以覆盖前面的结果。"
        hint-text=""
        apply-text="执行"
        reset-text="恢复默认"
        :loading="isRefreshing"
        @apply="applyDataProgram"
        @reset="resetDataProgram"
      />
    </div>

    <div class="filter-section front-filter-section" v-if="isGlobalGraph">
      <NoteProgramBar
        v-model="viewProgram"
        title="前端筛选"
        help-text="基于后端筛选的数据源实时筛选并渲染当前星系，修改后立即生效并保存。"
        hint-text=""
        apply-text="即时生效"
        reset-text="恢复默认"
        @apply="applyViewProgram"
        @reset="resetViewProgram"
      />
    </div>

    <!-- Middle: Graph -->
    <div class="graph-section" :style="{ height: graphHeight + 'px' }" ref="vueFlowWrapper">
      <VueFlow
        v-model="nodes"
        :edges="edges"
        :node-types="nodeTypes"
        :edge-types="edgeTypes"
        class="vue-flow-basic"
        :default-viewport="{ zoom: 1 }"
        :min-zoom="0.2"
        :max-zoom="4"
        :delete-key-code="['Backspace', 'Delete']"
        :zoom-on-double-click="false"
        @node-click="onNodeClick"
        @dblclick="onNativeDblClick"
        @connect="onConnect"
      >
        <Background />
        <Controls />
      </VueFlow>
      
      <div class="graph-toolbar">
        <el-button v-if="selectedEdgeId" type="danger" size="small" @click="deleteSelectedEdge">删除选中边</el-button>
        <el-button type="primary" size="small" :icon="Plus" @click="createNewNote">新建节点</el-button>
      </div>

      <!-- Height Resizer Handle -->
    <div class="graph-resizer" @mousedown="startResizing">
        <div class="resizer-indicator"></div>
    </div>
    
    <div class="mode-indicator" v-if="props.graphMode && props.graphMode !== 'global'">
        <el-tag effect="dark" :type="props.graphMode === 'satellite' ? 'success' : 'primary'">
            {{ props.graphMode === 'satellite' ? '卫星图 (Satellite View)' : '行星图 (Planetary View)' }}
        </el-tag>
        <el-button link :icon="Refresh" @click="refreshGraph" style="margin-left: 10px; color: #fff;">刷新</el-button>
    </div>
  </div>
    
    <!-- Bottom: Editor -->
    <div class="editor-section" :class="{ 'is-collapsed': !currentNoteId }">
      <NoteDetailPanel 
        v-if="currentNoteId" 
        :noteId="currentNoteId" 
        class="editor-wrapper"
        @update="handleNoteUpdate"
        @delete="handleNoteDelete"
        @create="handleNoteCreate"
      />
      <div v-else class="empty-state">
        <el-empty description="请在上方图表中选择一个节点" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router';
import { useUserStore } from '@/store/userStore';
import { markRaw, ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue';
import { Plus, Refresh, Delete } from '@element-plus/icons-vue';
import NoteDetailPanel from '@/components/NoteDetailPanel.vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import NoteProgramBar from '@/components/NoteProgramBar.vue';
import {
  useNoteStore,
  type NoteNode,
  applyNoteProgramChannelLocally,
  buildScanNoteProgramRequest,
  cloneNoteProgramChannel,
  createDefaultRecentMonthProgram,
  createIncludeAllProgram,
  normalizeNoteProgramChannel
} from '@/api/notes';
import { VueFlow, useVueFlow, Connection, MarkerType, type EdgeTypesObject, type NodeTypesObject } from '@vue-flow/core';
import { Background } from '@vue-flow/background';
import { Controls } from '@vue-flow/controls';
import CustomNode from '@/components/CustomNode.vue';
import ElkEdge from '@/components/ElkEdge.vue';
import { useLayout } from '@/utils/useLayout';

const nodeTypes: NodeTypesObject = {
  custom: markRaw(CustomNode) as NodeTypesObject[string],
};

const edgeTypes: EdgeTypesObject = {
  elk: markRaw(ElkEdge) as EdgeTypesObject[string],
};
import '@vue-flow/core/dist/style.css';
import '@vue-flow/core/dist/theme-default.css';
import '@vue-flow/controls/dist/style.css';

const props = defineProps<{
    tabId: string;
    targetNoteId?: string;
    graphMode?: 'global' | 'planetary' | 'satellite';
}>();

const router = useRouter();
const userStore = useUserStore();
const noteStore = useNoteStore();
const session = computed(() => noteStore.getTabSession(props.tabId));

// Computed source of truth for graph data
const sourceNotes = computed(() => {
    return noteStore.getTabNotes(props.tabId);
});

const sourceEdges = computed(() => {
    return noteStore.getTabEdges(props.tabId);
});

// Graph layout state
const graphHeight = ref(600);
const isResizing = ref(false);
const isManualResized = ref(false); // Track if user manually adjusted height
const startY = ref(0);
const startHeight = ref(0);

const calculateOptimalHeight = () => {
    const vh = window.innerHeight;
    const vw = window.innerWidth;
    const isPortrait = vh > vw;
    const reservedHeight = (!props.graphMode || props.graphMode === 'global') ? 320 : 220;
    
    const availableHeight = vh - reservedHeight;
    const minEditorHeight = 340;
    const maxGraphHeight = Math.max(200, availableHeight - minEditorHeight);

    if (isPortrait) {
        // Vertical screen: give more space to canvas (e.g. 70% of available)
        return Math.min(maxGraphHeight, Math.max(400, Math.floor(availableHeight * 0.7)));
    } else {
        // Horizontal screen: 50%
        return Math.min(maxGraphHeight, Math.max(300, Math.floor(availableHeight * 0.5)));
    }
};

const updateAdaptiveHeight = () => {
    if (!isManualResized.value) {
        graphHeight.value = calculateOptimalHeight();
    }
};

const startResizing = (e: MouseEvent) => {
    isResizing.value = true;
    isManualResized.value = true; // User manual resize overrides adaptive logic
    startY.value = e.clientY;
    startHeight.value = graphHeight.value;
    
    // Add temporary event listeners
    window.addEventListener('mousemove', handleResizing);
    window.addEventListener('mouseup', stopResizing);
    
    // Prevent text selection during drag
    document.body.style.userSelect = 'none';
};

const handleResizing = (e: MouseEvent) => {
    if (!isResizing.value) return;
    const delta = e.clientY - startY.value;
    const vh = window.innerHeight;
    const reservedHeight = (!props.graphMode || props.graphMode === 'global') ? 320 : 220;
    const availableHeight = vh - reservedHeight;
    const minEditorHeight = 340;
    const maxGraphHeight = Math.max(200, availableHeight - minEditorHeight);
    const newHeight = Math.max(200, Math.min(maxGraphHeight, startHeight.value + delta));
    graphHeight.value = newHeight;
};

const stopResizing = () => {
    isResizing.value = false;
    window.removeEventListener('mousemove', handleResizing);
    window.removeEventListener('mouseup', stopResizing);
    document.body.style.userSelect = '';
};

// Graph state
const nodes = ref<any[]>([]);
const edges = ref<any[]>([]);
const vueFlowWrapper = ref<HTMLElement | null>(null);
const { 
  onEdgesChange, 
  applyEdgeChanges, 
  onEdgeClick, 
  onPaneClick,
  project
} = useVueFlow();

const selectedEdgeId = ref<string | null>(null);

// Editor state
const currentNoteId = ref<string>('');
const isRefreshing = ref(false);
const isGraphUpdating = ref(false);
const isGlobalGraph = computed(() => !props.graphMode || props.graphMode === 'global');
const getAppliedDataProgram = () => normalizeNoteProgramChannel(
  session.value?.viewState.dataProgram ?? createDefaultRecentMonthProgram('start_at')
);
const getViewProgram = () => normalizeNoteProgramChannel(
  session.value?.viewState.viewProgram ?? createIncludeAllProgram()
);
const dataProgram = ref(normalizeNoteProgramChannel(getAppliedDataProgram()));
const viewProgram = ref(normalizeNoteProgramChannel(getViewProgram()));

const applyDataProgram = async () => {
  await refreshGraph(dataProgram.value, true);
};

const resetDataProgram = () => {
  dataProgram.value = createDefaultRecentMonthProgram('start_at');
};

const applyViewProgram = () => {
  viewProgram.value = cloneNoteProgramChannel(viewProgram.value);
};

const resetViewProgram = () => {
  viewProgram.value = createIncludeAllProgram();
};

const applyGraphFilters = async (force: boolean = false) => {
  if (!force && (isRefreshing.value || isGraphUpdating.value)) return;
  isGraphUpdating.value = true;
  try {
    const filteredNotes = isGlobalGraph.value
      ? applyNoteProgramChannelLocally(sourceNotes.value, viewProgram.value)
      : sourceNotes.value;
    const visibleNodeIds = new Set(filteredNotes.map(n => n.id));
    const filteredEdges = sourceEdges.value.filter(edge =>
      visibleNodeIds.has(edge.source_id) && visibleNodeIds.has(edge.target_id)
    );

    const graphNodes = filteredNotes.map(note => ({
      id: note.id,
      label: note.title || 'Untitled',
      position: { x: 0, y: 0 },
      data: {
        title: note.title,
        weight: note.weight,
        node_type: note.node_type,
        node_status: note.node_status,
        created_at: note.created_at,
        start_at: note.start_at
      },
      type: 'custom'
    }));

    const graphEdges = filteredEdges.map(edge => ({
      id: edge.id,
      source: edge.source_id,
      target: edge.target_id,
      label: edge.label,
      type: 'elk',
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 20,
        height: 20,
        color: '#909399'
      },
      style: { stroke: '#909399', strokeWidth: 1.5 }
    }));

    const layouted = await useLayout(graphNodes, graphEdges);

    edges.value = [];
    nodes.value = layouted.nodes;
    await nextTick();
    const finalNodeIds = new Set(nodes.value.map(n => String(n.id)));
    edges.value = layouted.edges.filter(edge =>
      finalNodeIds.has(String(edge.source)) && finalNodeIds.has(String(edge.target))
    );

    if (currentNoteId.value && !visibleNodeIds.has(currentNoteId.value)) {
      currentNoteId.value = '';
    }
  } finally {
    isGraphUpdating.value = false;
  }
};

const syncEdgesFromStore = async () => {
    if (isRefreshing.value) return;
    if (isGraphUpdating.value) {
        await nextTick();
        if (isGraphUpdating.value) return;
    }
    const nodeIds = new Set(nodes.value.map(n => String(n.id)));
    edges.value = sourceEdges.value
        .filter(e => nodeIds.has(e.source_id) && nodeIds.has(e.target_id))
        .map(e => ({
            id: e.id,
            source: e.source_id,
            target: e.target_id,
            label: e.label,
            type: 'elk',
            markerEnd: {
              type: MarkerType.ArrowClosed,
              width: 20,
              height: 20,
              color: '#909399',
            },
            style: { stroke: '#909399', strokeWidth: 1.5 },
            sourceHandle: e.source_handle,
            targetHandle: e.target_handle,
        }));
};

const selectNote = async (noteId: string) => {
  currentNoteId.value = noteId;
};

const handleNoteUpdate = (note: NoteNode) => {
    // Update graph node data
    const node = nodes.value.find(n => n.id === note.id);
    if (node) {
        node.label = note.title;
        if (!node.data) node.data = {};
        node.data.title = note.title;
        node.data.weight = note.weight;
        node.data.start_at = note.start_at;
        node.data.node_type = note.node_type;
        node.data.node_status = note.node_status;
    }
};

const handleNoteCreate = (note: NoteNode) => {
    noteStore.addNoteToTab(props.tabId, note.id);
    let pos = { x: Math.random() * 500, y: Math.random() * 300 };
    
    // Try to place near source node if possible
    if (currentNoteId.value) {
        const sourceNode = nodes.value.find(n => n.id === currentNoteId.value);
        if (sourceNode) {
            pos = {
                x: sourceNode.position.x + 50,
                y: sourceNode.position.y + 50
            };
        }
    }
    
    const newNode = {
      id: note.id,
      label: note.title,
      position: pos,
      data: { 
          title: note.title,
          weight: note.weight,
          node_type: note.node_type,
          node_status: note.node_status,
          created_at: note.created_at,
          start_at: note.start_at
      },
      type: 'custom'
    };
    nodes.value.push(newNode);
    selectNote(note.id);
};

const handleNoteDelete = (noteId: string) => {
    nodes.value = nodes.value.filter(n => n.id !== noteId);
    if (currentNoteId.value === noteId) {
        currentNoteId.value = '';
    }
};

watch(sourceEdges, async () => {
    await syncEdgesFromStore();
}, { deep: true });

watch(sourceNotes, async () => {
    if (!isRefreshing.value) {
        await applyGraphFilters(true);
    }
}, { deep: true });

watch(viewProgram, async (value) => {
    noteStore.updateTabViewState(props.tabId, {
        viewProgram: normalizeNoteProgramChannel(value)
    });

    if (isGlobalGraph.value && !isRefreshing.value) {
        await applyGraphFilters(true);
    }
}, { deep: true });

onMounted(async () => {
    updateAdaptiveHeight(); // Initial adaptive height
    window.addEventListener('resize', updateAdaptiveHeight);
    await refreshGraph();
});

onUnmounted(() => {
    window.removeEventListener('resize', updateAdaptiveHeight);
});

const refreshGraph = async (program = getAppliedDataProgram(), persist: boolean = false) => {
  if (isRefreshing.value) return;
  if (!userStore.isAuthenticated) {
    noteStore.clearTabData(props.tabId);
    nodes.value = [];
    edges.value = [];
    return;
  }
  isRefreshing.value = true;
  try {
    if (isGlobalGraph.value) {
      const normalizedProgram = normalizeNoteProgramChannel(program);
      await noteStore.queryNoteProgramForTab(props.tabId, buildScanNoteProgramRequest(normalizedProgram, {
          limit: 5000,
          include_edges: true
      }));
      if (persist) {
        noteStore.updateTabViewState(props.tabId, {
          dataProgram: normalizedProgram
        });
      }
    } else if (props.targetNoteId) {
      await noteStore.fetchConnectedComponentForTab(
          props.tabId,
          props.targetNoteId,
          props.graphMode === 'satellite' ? 'satellite' : 'planetary'
      );
    }

    await applyGraphFilters(true);
  } finally {
      isRefreshing.value = false;
  }
};

// Handle Connection
const onConnect = (params: Connection) => {
    if (!checkAuth()) return;
    // 确保连接对象包含箭头样式和具体的句柄 ID
    const edgeParams = {
        ...params,
        id: `e-${params.source}-${params.target}-${Date.now()}`,
        type: 'elk',
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 20,
          height: 20,
          color: '#909399',
        },
        style: { stroke: '#909399', strokeWidth: 1.5 },
        // 保留 handle 信息以支持当前会话的自定义连线
        sourceHandle: params.sourceHandle,
        targetHandle: params.targetHandle,
    };
    
    // UI 乐观更新
    edges.value.push(edgeParams);
    
    noteStore.createEdge(
        params.source,
        params.target,
        params.sourceHandle ?? undefined,
        params.targetHandle ?? undefined
    );
};

// Handle Edge Click
onEdgeClick((event) => {
    selectedEdgeId.value = event.edge.id;
});

// Handle Pane Click (Deselect)
onPaneClick(() => {
    selectedEdgeId.value = null;
});

// Delete Selected Edge
const deleteSelectedEdge = async () => {
    if (!selectedEdgeId.value) return;
    if (!checkAuth()) return;
    
    const edge = edges.value.find(e => e.id === selectedEdgeId.value);
    if (edge) {
        // UI 更新
        edges.value = edges.value.filter(e => e.id !== selectedEdgeId.value);
        // 后端同步
        await noteStore.deleteEdge(edge.source, edge.target);
        selectedEdgeId.value = null;
        ElMessage.success('边已删除');
    }
};

// Handle Edge Delete (Backspace/Delete key)
onEdgesChange((changes) => {
    if (!userStore.isAuthenticated && changes.some(c => c.type === 'remove')) {
        // Prevent deletion if not authenticated
        return;
    }
    changes.forEach((change) => {
        if (change.type === 'remove') {
            const edge = edges.value.find(e => e.id === change.id);
            if (edge) {
                noteStore.deleteEdge(edge.source, edge.target);
            }
            if (change.id === selectedEdgeId.value) {
                selectedEdgeId.value = null;
            }
        }
    });
    // Apply changes to local state and update ref
    edges.value = applyEdgeChanges(changes);
});

const onNodeClick = (event: any) => {
  selectNote(event.node.id);
};

const generateDefaultTitle = () => {
    const now = new Date();
    const yy = String(now.getFullYear()).slice(-2);
    const mm = String(now.getMonth() + 1).padStart(2, '0');
    const dd = String(now.getDate()).padStart(2, '0');
    const weekDays = ['日', '一', '二', '三', '四', '五', '六'];
    const weekDay = weekDays[now.getDay()];
    const hh = String(now.getHours()).padStart(2, '0');
    const min = String(now.getMinutes()).padStart(2, '0');
    
    return `${yy}${mm}${dd}周${weekDay}_${hh}${min}`;
};

const checkAuth = () => {
  if (!userStore.isAuthenticated) {
    ElMessageBox.confirm('该功能需要登录账号后才可用。是否前往登录？', '提示', {
      confirmButtonText: '前往登录',
      cancelButtonText: '取消',
      type: 'info'
    }).then(() => {
      router.push({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } });
    }).catch(() => {});
    return false;
  }
  return true;
};

const onNativeDblClick = (event: MouseEvent) => {
  // If clicked on a node or edge, do not create a new note
  const target = event.target as HTMLElement;
  if (target.closest('.vue-flow__node') || target.closest('.vue-flow__edge')) {
      return;
  }
  
  // Project screen coordinates to flow coordinates
  // Note: project() converts screen pixel coordinates to internal flow coordinates
  const bounds = vueFlowWrapper.value?.getBoundingClientRect();
  const projected = bounds
    ? project({ x: event.clientX - bounds.left, y: event.clientY - bounds.top })
    : project({ x: event.clientX, y: event.clientY });
  createNewNote(projected);
};

const createNewNote = async (targetPosition?: { x: number, y: number }) => {
  if (!checkAuth()) return;
  
  // If called from button click, targetPosition is MouseEvent
  let pos = targetPosition;
  if (pos && ((pos as any).preventDefault || (pos as any).type)) {
      pos = undefined;
  }
  
  if (!pos) {
       pos = { x: Math.random() * 500, y: Math.random() * 300 };
  }

  const defaultTitle = generateDefaultTitle();
  // Calculate center position or random
  const newNote = await noteStore.createNote(defaultTitle, '');
  if (newNote) {
    noteStore.addNoteToTab(props.tabId, newNote.id);
    // Add to graph
    const newNode = {
      id: newNote.id,
      label: newNote.title,
      position: pos,
      data: { 
          title: newNote.title,
          weight: newNote.weight,
          node_type: newNote.node_type,
          node_status: newNote.node_status,
          created_at: newNote.created_at,
          start_at: newNote.start_at
      },
      type: 'custom'
    };
    nodes.value.push(newNode);
    
    selectNote(newNote.id);
  }
};

</script>

<style scoped>
.task-manager-layout {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  overflow-x: hidden;
  overflow-y: auto;
}

.filter-section {
    padding: 16px 20px 12px;
    background: #fff;
    border-bottom: 1px solid #ebeef5;
    box-sizing: border-box;
}

.front-filter-section {
    padding-top: 0;
}

.graph-section {
  height: 600px; /* Default, overridden by :style */
  border-bottom: 1px solid #e6e6e6;
  position: relative;
  flex-shrink: 0; /* Don't let graph collapse */
  display: flex;
  flex-direction: column;
}

.vue-flow-basic {
  flex: 1;
}

.graph-resizer {
  height: 8px;
  width: 100%;
  background-color: #f5f7fa;
  cursor: ns-resize;
  position: absolute;
  bottom: 0;
  left: 0;
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 10;
  transition: background-color 0.2s;
  border-top: 1px solid #e6e6e6;
}

.graph-resizer:hover {
  background-color: #ecf5ff;
}

.resizer-indicator {
  width: 40px;
  height: 4px;
  border-top: 1px solid #dcdfe6;
  border-bottom: 1px solid #dcdfe6;
}

.graph-toolbar {
  position: absolute;
  top: 10px;
  right: 10px;
  z-index: 5;
  display: flex;
  gap: 10px;
  background: rgba(255, 255, 255, 0.8);
  padding: 5px;
  border-radius: 4px;
}

.mode-indicator {
    position: absolute;
    top: 10px;
    left: 10px;
    z-index: 5;
    background: rgba(0, 0, 0, 0.6);
    padding: 5px 10px;
    border-radius: 4px;
    display: flex;
    align-items: center;
}

/* Remove old filter panel styles */

.editor-section {
  flex: 1; /* Take remaining space but can grow */
  display: flex;
  flex-direction: column;
  background-color: #fff;
  min-height: 600px; /* Increased from 400px */
  overflow-y: auto; /* Allow content to grow and scroll */
}

.editor-section.is-collapsed {
    /* Optional: shrink if no task selected? */
}

.editor-wrapper {
  display: flex;
  flex-direction: column;
  height: auto; /* Changed from 100% to auto */
  padding: 20px;
}

.editor-header {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 20px;
  padding-bottom: 15px;
  border-bottom: 1px solid #f0f0f0;
}

.header-row-primary {
    display: flex;
    align-items: center;
    gap: 10px;
}

.header-row-secondary {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 12px;
    color: #909399;
}

.header-row-tertiary {
    display: flex;
    align-items: center;
}

.meta-group {
    display: flex;
    gap: 15px;
}

.title-input {
  flex: 1;
  font-size: 18px;
}

.time-tag {
  display: flex;
  align-items: center;
  gap: 4px;
  cursor: default;
}

.weight-control {
    width: 150px;
    margin-right: 20px;
    display: flex;
    align-items: center;
    gap: 10px;
}

.weight-control .label {
    font-size: 12px;
    color: #606266;
    white-space: nowrap;
}

.weight-control .el-slider {
    flex: 1;
}

.status-control {
    display: flex;
    align-items: center;
    gap: 10px;
}

.status-control .label {
    font-size: 12px;
    color: #606266;
    white-space: nowrap;
}

.help-icon {
    margin-left: 5px;
    font-size: 14px;
    color: #909399;
    cursor: help;
}

.history-toggle {
    margin-left: auto;
}

.history-panel {
    margin-top: 15px;
    padding: 10px;
    background: #f8f9fb;
    border-radius: 4px;
    max-height: 200px;
    overflow-y: auto;
    font-size: 13px;
    border: 1px solid #ebeef5;
}

.history-empty {
    text-align: center;
    color: #909399;
    padding: 10px;
}

.history-item {
    display: flex;
    align-items: flex-start;
    gap: 15px;
    padding: 6px 0;
    border-bottom: 1px dashed #ebeef5;
}

.history-item:last-child {
    border-bottom: none;
}

.history-time {
    color: #909399;
    white-space: nowrap;
    font-family: monospace;
}

.history-content {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
}

.field-tag {
    min-width: 40px;
    text-align: center;
}

.history-value {
    color: #303133;
    word-break: break-all;
}

.save-status {
    font-size: 12px;
    margin-right: 15px;
    display: flex;
    align-items: center;
}

.status-saved {
    color: #67c23a;
    display: flex;
    align-items: center;
    gap: 4px;
}

.status-saving {
    color: #e6a23c;
    display: flex;
    align-items: center;
    gap: 4px;
}

.status-unsaved {
    color: #909399;
}

.empty-state {
    display: flex;
    justify-content: center;
    align-items: center;
    height: 100%;
    color: #909399;
}
</style>
