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

    <NoteSplitView
      class="notes-workspace"
      :top-height="graphHeight"
      :show-editor="Boolean(currentNoteId)"
      empty-description="请在上方图表中选择一个节点"
      @resize-start="startResizing"
    >
      <template #main>
        <div class="graph-section" ref="vueFlowWrapper">
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

          <div class="mode-indicator" v-if="props.graphMode && props.graphMode !== 'global'">
            <el-tag effect="dark" :type="props.graphMode === 'satellite' ? 'success' : 'primary'">
              {{ props.graphMode === 'satellite' ? '卫星图 (Satellite View)' : '行星图 (Planetary View)' }}
            </el-tag>
            <el-button link :icon="Refresh" class="mode-refresh-button" @click="refreshGraph">刷新</el-button>
          </div>
        </div>
      </template>

      <template #editor>
        <NoteDetailPanel
          :noteId="currentNoteId"
          @update="handleNoteUpdate"
          @delete="handleNoteDelete"
          @create="handleNoteCreate"
        />
      </template>
    </NoteSplitView>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router';
import { useUserStore } from '@/store/userStore';
import { markRaw, ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue';
import { Plus, Refresh } from '@element-plus/icons-vue';
import NoteDetailPanel from '@/components/NoteDetailPanel.vue';
import NoteSplitView from '@/components/NoteSplitView.vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import NoteProgramBar from '@/components/NoteProgramBar.vue';
import {
  useNoteStore,
  type NoteEdge,
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
import { useResizablePane } from '@/utils/useResizablePane';

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

const calculateGraphBounds = () => {
    const vh = window.innerHeight;
    const vw = window.innerWidth;
    const isPortrait = vh > vw;
    const reservedHeight = (!props.graphMode || props.graphMode === 'global') ? 320 : 220;
    const availableHeight = vh - reservedHeight;
    const minEditorHeight = 340;
    const maxGraphHeight = Math.max(200, availableHeight - minEditorHeight);
    const adaptiveHeight = isPortrait
        ? Math.min(maxGraphHeight, Math.max(400, Math.floor(availableHeight * 0.7)))
        : Math.min(maxGraphHeight, Math.max(300, Math.floor(availableHeight * 0.5)));

    return {
        adaptiveHeight,
        maxGraphHeight,
    };
};

const {
    paneHeight: graphHeight,
    startResizing,
} = useResizablePane({
    initialHeight: 600,
    getAdaptiveHeight: () => calculateGraphBounds().adaptiveHeight,
    getResizeBounds: () => ({
        min: 200,
        max: calculateGraphBounds().maxGraphHeight,
    }),
});

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
let graphFilterQueued = false;
let graphFilterTimer: ReturnType<typeof setTimeout> | null = null;
const isGlobalGraph = computed(() => !props.graphMode || props.graphMode === 'global');
const getAppliedDataProgram = () => normalizeNoteProgramChannel(
  session.value?.viewState.dataProgram ?? createDefaultRecentMonthProgram('start_at')
);
const getViewProgram = () => normalizeNoteProgramChannel(
  session.value?.viewState.viewProgram ?? createIncludeAllProgram()
);
const dataProgram = ref(normalizeNoteProgramChannel(getAppliedDataProgram()));
const viewProgram = ref(normalizeNoteProgramChannel(getViewProgram()));

const buildGraphEdge = (
  edge: Pick<NoteEdge, 'id' | 'source_id' | 'target_id' | 'label' | 'source_handle' | 'target_handle'>
) => ({
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
  style: { stroke: '#909399', strokeWidth: 1.5 },
  sourceHandle: edge.source_handle,
  targetHandle: edge.target_handle
});

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

const scheduleGraphFilterApply = (delay: number = 120) => {
  if (graphFilterTimer) {
    clearTimeout(graphFilterTimer);
  }
  graphFilterTimer = setTimeout(() => {
    graphFilterTimer = null;
    void applyGraphFilters();
  }, delay);
};

const applyGraphFilters = async (force: boolean = false) => {
  if (!force && isRefreshing.value) return;
  if (isGraphUpdating.value) {
    graphFilterQueued = true;
    return;
  }
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

    const graphEdges = filteredEdges.map(edge => buildGraphEdge(edge));

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
    if (graphFilterQueued) {
      graphFilterQueued = false;
      void applyGraphFilters(true);
    }
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
        .map(e => buildGraphEdge(e));
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
        scheduleGraphFilterApply();
    }
}, { deep: true });

onMounted(async () => {
    await refreshGraph();
});

onUnmounted(() => {
    if (graphFilterTimer) {
        clearTimeout(graphFilterTimer);
        graphFilterTimer = null;
    }
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
const onConnect = async (params: Connection) => {
    if (!checkAuth()) return;
    const tempEdge = buildGraphEdge({
        id: `e-${params.source}-${params.target}-${Date.now()}`,
        source_id: params.source,
        target_id: params.target,
        source_handle: params.sourceHandle ?? undefined,
        target_handle: params.targetHandle ?? undefined,
    });

    edges.value.push(tempEdge);

    const persistedEdge = await noteStore.createEdge(
        params.source,
        params.target,
        params.sourceHandle ?? undefined,
        params.targetHandle ?? undefined
    );

    if (!persistedEdge) {
        edges.value = edges.value.filter(edge => edge.id !== tempEdge.id);
        ElMessage.error('创建边失败，已回滚');
        return;
    }

    edges.value = edges.value.map(edge => (
        edge.id === tempEdge.id
            ? buildGraphEdge(persistedEdge)
            : edge
    ));
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
        const previousEdge = { ...edge };
        edges.value = edges.value.filter(e => e.id !== selectedEdgeId.value);
        const success = await noteStore.deleteEdge(edge.source, edge.target);
        if (!success) {
            if (!edges.value.some(item => item.id === previousEdge.id)) {
                edges.value = [...edges.value, previousEdge];
            }
            ElMessage.error('删除边失败，已恢复');
            return;
        }
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
                void noteStore.deleteEdge(edge.source, edge.target).then(success => {
                    if (success) return;

                    if (!edges.value.some(item => item.id === edge.id)) {
                        edges.value = [...edges.value, edge];
                    }
                    ElMessage.error('删除边失败，已恢复');
                });
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
  height: 100%;
  min-height: 0;
  overflow-x: hidden;
  overflow-y: hidden;
}

.filter-section {
    padding: 16px 20px 12px;
    background: #fff;
    border-bottom: 1px solid #ebeef5;
    box-sizing: border-box;
    flex-shrink: 0;
}

.front-filter-section {
    padding-top: 0;
}

.notes-workspace {
  flex: 1;
  min-height: 0;
}

.graph-section {
  height: 100%;
  border-bottom: 1px solid #e6e6e6;
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.vue-flow-basic {
  flex: 1;
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

.mode-refresh-button {
  margin-left: 10px;
  color: #fff;
}
</style>
