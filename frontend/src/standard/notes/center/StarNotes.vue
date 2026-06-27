<template>
  <div class="task-manager-layout">
    <div class="filter-section" v-if="isGlobalGraph">
      <NoteProgramBar
        v-model="dataProgram"
        title="后端筛选"
        help-text="决定从后端加载哪些节点，点击“执行”后生效并保存；规则按顺序执行，后面的添加/移除/筛选可以覆盖前面的结果。"
        hint-text=""
        apply-text="执行"
        reset-text="恢复默认"
        enable-full-text
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
            <el-button size="small" :icon="Refresh" :loading="isGraphUpdating || isRefreshing" @click="relayoutGraph">重新排版</el-button>
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
          :key="`${currentNoteId}:${editorRefreshVersion}`"
          :noteId="currentNoteId"
          editor-layout="fill"
          @update="handleNoteUpdate"
          @delete="handleNoteDelete"
          @create="handleNoteCreate"
        />
      </template>
    </NoteSplitView>
  </div>
</template>

<script setup lang="ts">
import { markRaw, ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue';
import { Plus, Refresh } from '@element-plus/icons-vue';
import NoteDetailPanel from '@/components/NoteDetailPanel.vue';
import NoteSplitView from '@/components/NoteSplitView.vue';
import { ElMessage } from 'element-plus';
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
  areNoteRequestsEquivalent,
  noteProgramChannelNeedsCustomFieldsLocally,
  noteKey,
  normalizeNoteProgramChannel
} from '@/api/notes';
import { VueFlow, useVueFlow, useNodesInitialized, Connection, MarkerType, type EdgeTypesObject, type NodeTypesObject } from '@vue-flow/core';
import { Background } from '@vue-flow/background';
import { Controls } from '@vue-flow/controls';
import CustomNode from '@/components/CustomNode.vue';
import ElkEdge from '@/components/ElkEdge.vue';
import { useLayout } from '@/utils/useLayout';
import { buildOrthogonalSegments, routeOrthogonalEdge } from '@/utils/orthogonalEdgeRouter';
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
    active?: boolean;
    targetNoteId?: string;
    graphMode?: 'global' | 'planetary' | 'satellite';
}>();

const noteStore = useNoteStore();
const session = computed(() => noteStore.getTabSession(props.tabId));

// Computed source of truth for graph data
const sourceNotes = computed(() => {
    return noteStore.getTabNotes(props.tabId);
});

const sourceEdges = computed(() => {
    return noteStore.getTabEdges(props.tabId);
});

const sourceNotesVersion = computed(() => `${session.value?.noteDataVersion ?? 0}:${noteStore.noteRevision}`);
const sourceEdgesVersion = computed(() => `${session.value?.edgeDataVersion ?? 0}:${noteStore.edgeRevision}`);

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
const nodesInitialized = useNodesInitialized();
const { 
  onEdgesChange, 
  onNodeDrag,
  onNodeDragStop,
  applyEdgeChanges, 
  onEdgeClick, 
  onPaneClick,
  project,
  updateNodeInternals
} = useVueFlow();

const selectedEdgeId = ref<string | null>(null);
const nodePositionCache = ref<Record<string, { x: number; y: number }>>({});
const edgeHandleCache = ref<Record<string, { sourceHandle?: string; targetHandle?: string }>>({});
const edgeRouteCache = ref<Record<string, any[]>>({});
const edgeLocalRouteCache = ref<Record<string, Array<{ x: number; y: number }>>>({});
let suppressNodePositionWatch = false;
let dragRerouteFrame: number | null = null;
let pendingDragEdgeIds = new Set<string>();

// Editor state
const currentNoteId = ref<string>('');
const editorRefreshVersion = ref(0);
const isRefreshing = ref(false);
const isGraphUpdating = ref(false);
let graphFilterQueued = false;
let graphRelayoutQueued = false;
let inactiveGraphRefreshPending = false;
let graphFilterTimer: ReturnType<typeof setTimeout> | null = null;
let deferredInitialRelayoutTimer: ReturnType<typeof setTimeout> | null = null;
const isGlobalGraph = computed(() => !props.graphMode || props.graphMode === 'global');
const isActive = computed(() => props.active !== false);
const getAppliedDataProgram = () => normalizeNoteProgramChannel(
  session.value?.viewState.dataProgram ?? createDefaultRecentMonthProgram('start_at')
);
const getViewProgram = () => normalizeNoteProgramChannel(
  session.value?.viewState.viewProgram ?? createIncludeAllProgram()
);
const dataProgram = ref(normalizeNoteProgramChannel(getAppliedDataProgram()));
const viewProgram = ref(normalizeNoteProgramChannel(getViewProgram()));
const graphViewNeedsCustomFields = computed(() => noteProgramChannelNeedsCustomFieldsLocally(viewProgram.value));
const AUTO_LAYOUT_NODE_LIMIT = 300;
const AUTO_LAYOUT_EDGE_LIMIT = 600;
const DETAILED_EDGE_ROUTING_NODE_LIMIT = 600;
const DETAILED_EDGE_ROUTING_EDGE_LIMIT = 1000;
const GLOBAL_GRAPH_CACHE_TTL_MS = 60_000;
const VERTICAL_HANDLE_THRESHOLD = 60;
const HORIZONTAL_HANDLE_THRESHOLD = 60;

const resolveRelativeEdgeHandles = (
  sourceNode?: { position?: { x?: number; y?: number } } | null,
  targetNode?: { position?: { x?: number; y?: number } } | null
) => {
  if (!sourceNode || !targetNode) {
    return {
      sourceHandle: undefined,
      targetHandle: undefined
    };
  }

  const dx = (Number(targetNode.position?.x) || 0) - (Number(sourceNode.position?.x) || 0);
  const dy = (Number(targetNode.position?.y) || 0) - (Number(sourceNode.position?.y) || 0);

  if (dy >= VERTICAL_HANDLE_THRESHOLD) {
    return { sourceHandle: 'b-s', targetHandle: 't-t' };
  }

  if (dy <= -VERTICAL_HANDLE_THRESHOLD) {
    return { sourceHandle: 't-s', targetHandle: 'b-t' };
  }

  if (dx >= HORIZONTAL_HANDLE_THRESHOLD) {
    return { sourceHandle: 'r-s', targetHandle: 'l-t' };
  }

  if (dx <= -HORIZONTAL_HANDLE_THRESHOLD) {
    return { sourceHandle: 'l-s', targetHandle: 'r-t' };
  }

  if (Math.abs(dy) >= Math.abs(dx)) {
    return dy >= 0
      ? { sourceHandle: 'b-s', targetHandle: 't-t' }
      : { sourceHandle: 't-s', targetHandle: 'b-t' };
  }

  return dx >= 0
    ? { sourceHandle: 'r-s', targetHandle: 'l-t' }
    : { sourceHandle: 'l-s', targetHandle: 'r-t' };
};

const resolveGraphEdgeHandles = (
  edge: Pick<NoteEdge, 'id' | 'source_id' | 'target_id' | 'source_handle' | 'target_handle'>,
  options?: {
    nodeLookup?: Map<string, any>;
    preferDynamicHandles?: boolean;
  }
) => {
  let sourceHandle = edge.source_handle;
  let targetHandle = edge.target_handle;

  const relativeHandles = options?.nodeLookup
    ? resolveRelativeEdgeHandles(
        options.nodeLookup.get(noteKey(edge.source_id)),
        options.nodeLookup.get(noteKey(edge.target_id))
      )
    : { sourceHandle: undefined, targetHandle: undefined };

  const cachedHandles = edgeHandleCache.value[edge.id];

  if (options?.preferDynamicHandles) {
    sourceHandle = sourceHandle ?? relativeHandles.sourceHandle ?? cachedHandles?.sourceHandle;
    targetHandle = targetHandle ?? relativeHandles.targetHandle ?? cachedHandles?.targetHandle;
  }

  sourceHandle = sourceHandle ?? cachedHandles?.sourceHandle ?? relativeHandles.sourceHandle;
  targetHandle = targetHandle ?? cachedHandles?.targetHandle ?? relativeHandles.targetHandle;

  return {
    sourceHandle,
    targetHandle
  };
};

const buildGraphEdge = (
  edge: Pick<NoteEdge, 'id' | 'source_id' | 'target_id' | 'label' | 'source_handle' | 'target_handle'>,
  options?: {
    nodeLookup?: Map<string, any>;
    preferDynamicHandles?: boolean;
    includeLocalRoute?: boolean;
  }
) => ({
  ...resolveGraphEdgeHandles(edge, options),
  id: edge.id,
  source: noteKey(edge.source_id),
  target: noteKey(edge.target_id),
  label: edge.label,
  type: 'elk',
  data:
    edgeRouteCache.value[edge.id] || edgeLocalRouteCache.value[edge.id]
      ? {
          elkSections: edgeRouteCache.value[edge.id],
          routePoints: options?.includeLocalRoute === false ? undefined : edgeLocalRouteCache.value[edge.id]
        }
      : undefined,
  markerEnd: {
    type: MarkerType.ArrowClosed,
    width: 20,
    height: 20,
    color: '#909399'
  },
  style: { stroke: '#909399', strokeWidth: 1.5 }
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

const buildGlobalGraphRequest = (program = getAppliedDataProgram()) => (
  buildScanNoteProgramRequest(normalizeNoteProgramChannel(program), {
    include_custom_fields: graphViewNeedsCustomFields.value,
    limit: 5000,
    include_edges: true
  })
);

const currentGlobalGraphIncludesCustomFields = () => {
  const lastQuery = session.value?.lastQuery as { result?: { include_custom_fields?: boolean } } | null | undefined;
  return Boolean(lastQuery?.result?.include_custom_fields);
};

const canUseCachedGlobalGraph = (program = getAppliedDataProgram()) => {
  if (!isGlobalGraph.value) return false;
  const currentSession = session.value;
  if (!currentSession || currentSession.noteIds.length === 0) return false;
  return areNoteRequestsEquivalent(currentSession.lastQuery, buildGlobalGraphRequest(program));
};

const isCachedGlobalGraphFresh = () => {
  const loadedAt = session.value?.lastLoadedAt || 0;
  return loadedAt > 0 && Date.now() - loadedAt <= GLOBAL_GRAPH_CACHE_TTL_MS;
};

const scheduleGraphFilterApply = (delay: number = 120) => {
  if (graphFilterTimer) {
    clearTimeout(graphFilterTimer);
  }
  graphFilterTimer = setTimeout(() => {
    graphFilterTimer = null;
    void applyGraphFilters(false, false);
  }, delay);
};

const clearDeferredInitialRelayout = () => {
  if (deferredInitialRelayoutTimer) {
    clearTimeout(deferredInitialRelayoutTimer);
    deferredInitialRelayoutTimer = null;
  }
};

const hasCachedNodePositions = () => Object.keys(nodePositionCache.value).length > 0;

const shouldDeferAutoRelayout = (hadCachedNodePositions: boolean = hasCachedNodePositions()) => (
  isGlobalGraph.value
  && shouldAutoRelayoutGraph()
  && !hadCachedNodePositions
);

const scheduleDeferredInitialRelayout = () => {
  clearDeferredInitialRelayout();
  deferredInitialRelayoutTimer = setTimeout(() => {
    deferredInitialRelayoutTimer = null;
    if (!isActive.value || isRefreshing.value || isGraphUpdating.value) {
      scheduleDeferredInitialRelayout();
      return;
    }
    void applyGraphFilters(true, true);
  }, 0);
};

const waitForAnimationFrame = () =>
  new Promise<void>(resolve => {
    window.requestAnimationFrame(() => resolve());
  });

const waitForNodesReady = async () => {
  if (nodes.value.length === 0) {
    return;
  }

  if (nodesInitialized.value) {
    return;
  }

  await new Promise<void>(resolve => {
    const stop = watch(
      nodesInitialized,
      value => {
        if (value) {
          stop();
          resolve();
        }
      },
      { immediate: true }
    );

    window.setTimeout(() => {
      stop();
      resolve();
    }, 500);
  });
};

const refreshNodeInternals = async (nodeIds?: string[]) => {
  await nextTick();
  await waitForAnimationFrame();
  updateNodeInternals(nodeIds);
  await nextTick();
  await waitForNodesReady();
};

const refreshRenderedEdges = (graphEdges = edges.value) => {
  edges.value = graphEdges.map(edge => ({
    ...edge,
    data: edge.data ? { ...edge.data } : edge.data
  }));
};

const cacheNodePositions = (graphNodes = nodes.value) => {
  const nextCache = { ...nodePositionCache.value };
  graphNodes.forEach(node => {
    const id = String(node.id);
    nextCache[id] = {
      x: Number(node.position?.x) || 0,
      y: Number(node.position?.y) || 0
    };
  });
  nodePositionCache.value = nextCache;
};

const cacheDraggedNodePositions = (graphNodes: Array<{ id: string; position?: { x?: number; y?: number } }> = []) => {
  if (graphNodes.length === 0) return;

  const nextCache = { ...nodePositionCache.value };
  graphNodes.forEach(node => {
    const id = String(node.id);
    nextCache[id] = {
      x: Number(node.position?.x) || 0,
      y: Number(node.position?.y) || 0
    };
  });
  nodePositionCache.value = nextCache;
};

const buildNodeLookup = (
  graphNodes = nodes.value,
  options: {
    useCachedPositions?: boolean;
  } = {}
) => {
  const useCachedPositions = options.useCachedPositions ?? false;

  return new Map(graphNodes.map(node => {
    const id = String(node.id);
    const cachedPosition = useCachedPositions ? nodePositionCache.value[id] : null;

    if (!cachedPosition) {
      return [id, node];
    }

    return [id, {
      ...node,
      position: {
        ...(node.position ?? {}),
        x: cachedPosition.x,
        y: cachedPosition.y
      }
    }];
  }));
};

const cacheEdgeHandles = (graphEdges = edges.value) => {
  const nextCache = { ...edgeHandleCache.value };
  graphEdges.forEach(edge => {
    nextCache[String(edge.id)] = {
      sourceHandle: edge.sourceHandle,
      targetHandle: edge.targetHandle
    };
  });
  edgeHandleCache.value = nextCache;
};

const cacheEdgeRoutes = (graphEdges = edges.value) => {
  const nextCache = { ...edgeRouteCache.value };
  graphEdges.forEach(edge => {
    if (Array.isArray(edge.data?.elkSections) && edge.data.elkSections.length > 0) {
      nextCache[String(edge.id)] = edge.data.elkSections;
    }
  });
  edgeRouteCache.value = nextCache;
};

const cacheLocalEdgeRoutes = (graphEdges = edges.value) => {
  const nextCache = { ...edgeLocalRouteCache.value };
  graphEdges.forEach(edge => {
    if (Array.isArray(edge.data?.routePoints) && edge.data.routePoints.length > 1) {
      nextCache[String(edge.id)] = edge.data.routePoints;
    }
  });
  edgeLocalRouteCache.value = nextCache;
};

const clearLocalEdgeRoutes = (edgeIds: string[]) => {
  if (edgeIds.length === 0) return;
  const nextCache = { ...edgeLocalRouteCache.value };
  edgeIds.forEach(edgeId => {
    delete nextCache[edgeId];
  });
  edgeLocalRouteCache.value = nextCache;
};

const removeEdgeCaches = (edgeIds: string[]) => {
  if (edgeIds.length === 0) return;

  const nextHandleCache = { ...edgeHandleCache.value };
  const nextElkRouteCache = { ...edgeRouteCache.value };
  const nextLocalRouteCache = { ...edgeLocalRouteCache.value };

  edgeIds.forEach(edgeId => {
    delete nextHandleCache[edgeId];
    delete nextElkRouteCache[edgeId];
    delete nextLocalRouteCache[edgeId];
  });

  edgeHandleCache.value = nextHandleCache;
  edgeRouteCache.value = nextElkRouteCache;
  edgeLocalRouteCache.value = nextLocalRouteCache;
};

const extractHintPointsFromSections = (sections: any[] | undefined) => {
  if (!Array.isArray(sections) || sections.length === 0) return [] as Array<{ x: number; y: number }>;

  const points: Array<{ x: number; y: number }> = [];
  sections.forEach(section => {
    if (section?.startPoint) {
      points.push({ x: section.startPoint.x, y: section.startPoint.y });
    }
    if (Array.isArray(section?.bendPoints)) {
      section.bendPoints.forEach((point: any) => {
        points.push({ x: point.x, y: point.y });
      });
    }
    if (section?.endPoint) {
      points.push({ x: section.endPoint.x, y: section.endPoint.y });
    }
  });
  return points;
};

const getEdgeHintPoints = (edgeId: string) => {
  const localRoute = edgeLocalRouteCache.value[edgeId];
  if (Array.isArray(localRoute) && localRoute.length > 1) {
    return localRoute;
  }
  return extractHintPointsFromSections(edgeRouteCache.value[edgeId]);
};

const withRoutePointsFromElk = (graphEdges: any[]) =>
  graphEdges.map(edge => {
    const routePoints = extractHintPointsFromSections(edge.data?.elkSections);
    if (routePoints.length <= 1) {
      return edge;
    }

    return {
      ...edge,
      data: {
        ...(edge.data ?? {}),
        routePoints
      }
    };
  });

const getStoredRoutePoints = (edge: any, routeCache: Record<string, Array<{ x: number; y: number }>>) => {
  const edgeId = String(edge.id);
  const cachedRoute = routeCache[edgeId];
  if (Array.isArray(cachedRoute) && cachedRoute.length > 1) {
    return cachedRoute;
  }

  const routePoints = edge.data?.routePoints;
  if (Array.isArray(routePoints) && routePoints.length > 1) {
    return routePoints;
  }

  const hintPoints = getEdgeHintPoints(edgeId);
  return hintPoints.length > 1 ? hintPoints : null;
};

const getRenderedEdgeRoutePoints = (edge: any) => {
  const routePoints = edge.data?.routePoints;
  if (Array.isArray(routePoints) && routePoints.length > 1) {
    return routePoints;
  }

  const elkRoutePoints = extractHintPointsFromSections(edge.data?.elkSections);
  return elkRoutePoints.length > 1 ? elkRoutePoints : null;
};

const compareEdgesForRouting = (edgeA: any, edgeB: any, nodeLookup: Map<string, any>) => {
  const sourceA = nodeLookup.get(String(edgeA.source));
  const sourceB = nodeLookup.get(String(edgeB.source));
  const targetA = nodeLookup.get(String(edgeA.target));
  const targetB = nodeLookup.get(String(edgeB.target));

  const sourceAY = Number(sourceA?.position?.y) || 0;
  const sourceBY = Number(sourceB?.position?.y) || 0;
  if (sourceAY !== sourceBY) return sourceAY - sourceBY;

  const sourceAX = Number(sourceA?.position?.x) || 0;
  const sourceBX = Number(sourceB?.position?.x) || 0;
  if (sourceAX !== sourceBX) return sourceAX - sourceBX;

  const targetAY = Number(targetA?.position?.y) || 0;
  const targetBY = Number(targetB?.position?.y) || 0;
  if (targetAY !== targetBY) return targetAY - targetBY;

  const targetAX = Number(targetA?.position?.x) || 0;
  const targetBX = Number(targetB?.position?.x) || 0;
  if (targetAX !== targetBX) return targetAX - targetBX;

  return String(edgeA.id).localeCompare(String(edgeB.id));
};

const routeGraphEdges = (
  graphNodes: any[],
  graphEdges: any[],
  options: {
    rerouteEdgeIds?: Set<string>;
    rerouteAll?: boolean;
  } = {}
) => {
  const visibleNodeLookup = new Map(graphNodes.map(node => [String(node.id), node]));
  const nextLocalRouteCache = { ...edgeLocalRouteCache.value };
  const edgeIdsToRoute = options.rerouteAll
    ? new Set(graphEdges.map(edge => String(edge.id)))
    : options.rerouteEdgeIds
      ? options.rerouteEdgeIds
      : new Set(
          graphEdges
            .filter(edge =>
              (!Array.isArray(nextLocalRouteCache[String(edge.id)]) || nextLocalRouteCache[String(edge.id)].length <= 1) &&
              !Array.isArray(edge.data?.elkSections)
            )
            .map(edge => String(edge.id))
        );
  const occupiedSegments: ReturnType<typeof buildOrthogonalSegments> = [];
  const routedEdgeMap = new Map<string, any>();
  const routingQueue = [...graphEdges].sort((edgeA, edgeB) => compareEdgesForRouting(edgeA, edgeB, visibleNodeLookup));

  routingQueue.forEach(edge => {
    const edgeId = String(edge.id);
    if (!edgeIdsToRoute.has(edgeId)) {
      const routePoints = getStoredRoutePoints(edge, nextLocalRouteCache);
      if (routePoints) {
        occupiedSegments.push(...buildOrthogonalSegments(routePoints));
        routedEdgeMap.set(edgeId, {
          ...edge,
          data: {
            ...(edge.data ?? {}),
            routePoints
          }
        });
        return;
      }

      routedEdgeMap.set(edgeId, edge);
      return;
    }

    const routePoints = routeOrthogonalEdge(visibleNodeLookup, edge, {
      hintPoints: getEdgeHintPoints(edgeId),
      occupiedSegments
    });

    if (routePoints && routePoints.length > 1) {
      nextLocalRouteCache[edgeId] = routePoints;
      occupiedSegments.push(...buildOrthogonalSegments(routePoints));
      routedEdgeMap.set(edgeId, {
        ...edge,
        data: {
          ...(edge.data ?? {}),
          routePoints
        }
      });
      return;
    }

    delete nextLocalRouteCache[edgeId];
    routedEdgeMap.set(edgeId, edge);
  });

  const routedEdges = graphEdges.map(edge => routedEdgeMap.get(String(edge.id)) ?? edge);

  edgeLocalRouteCache.value = nextLocalRouteCache;
  return routedEdges;
};

const shouldUseDetailedEdgeRouting = (graphNodes: any[], graphEdges: any[]) => (
  graphNodes.length <= DETAILED_EDGE_ROUTING_NODE_LIMIT
  && graphEdges.length <= DETAILED_EDGE_ROUTING_EDGE_LIMIT
);

const rerouteVisibleEdgeSubset = (
  rerouteEdgeIds: Set<string>
) => {
  if (rerouteEdgeIds.size === 0) {
    return;
  }

  const nodeLookup = buildNodeLookup(nodes.value, {
    useCachedPositions: true
  });
  const currentEdges = edges.value;
  const currentEdgeIds = new Set(currentEdges.map(edge => String(edge.id)));
  const nextLocalRouteCache = { ...edgeLocalRouteCache.value };
  const occupiedSegments: ReturnType<typeof buildOrthogonalSegments> = [];
  const edgeIndexMap = new Map(currentEdges.map((edge, index) => [String(edge.id), index]));

  const affectedEdges = sourceEdges.value
    .filter(edge =>
      rerouteEdgeIds.has(String(edge.id)) &&
      currentEdgeIds.has(String(edge.id)) &&
      nodeLookup.has(noteKey(edge.source_id)) &&
      nodeLookup.has(noteKey(edge.target_id))
    )
    .map(edge => buildGraphEdge(edge, {
      nodeLookup,
      preferDynamicHandles: true
    }))
    .sort((edgeA, edgeB) => compareEdgesForRouting(edgeA, edgeB, nodeLookup));

  const reroutedEdgeMap = new Map<string, any>();

  affectedEdges.forEach(edge => {
    const edgeId = String(edge.id);
    const routePoints = routeOrthogonalEdge(nodeLookup, edge, {
      hintPoints: getEdgeHintPoints(edgeId),
      occupiedSegments,
      includeOccupiedCoordinates: false
    });

    if (routePoints && routePoints.length > 1) {
      nextLocalRouteCache[edgeId] = routePoints;
      reroutedEdgeMap.set(edgeId, {
        ...edge,
        data: {
          ...(edge.data ?? {}),
          routePoints
        }
      });
      return;
    }

    delete nextLocalRouteCache[edgeId];
    reroutedEdgeMap.set(edgeId, edge);
  });

  if (reroutedEdgeMap.size === 0) {
    return;
  }

  const nextEdges = currentEdges.slice();

  reroutedEdgeMap.forEach((edge, edgeId) => {
    const index = edgeIndexMap.get(edgeId);
    if (index === undefined) {
      return;
    }
    nextEdges[index] = edge;
  });

  edges.value = nextEdges;
  edgeLocalRouteCache.value = nextLocalRouteCache;
  cacheEdgeHandles(nextEdges);
  cacheLocalEdgeRoutes(nextEdges);
};

const rebuildVisibleEdges = (rerouteEdgeIds?: Set<string>) => {
  const nodeIds = new Set(nodes.value.map(node => String(node.id)));
  const nodeLookup = new Map(nodes.value.map(node => [String(node.id), node]));
  const graphEdges = sourceEdges.value
    .filter(edge => nodeIds.has(noteKey(edge.source_id)) && nodeIds.has(noteKey(edge.target_id)))
    .map(edge => buildGraphEdge(edge, {
      nodeLookup,
      preferDynamicHandles: true
    }));

  const nextEdges = shouldUseDetailedEdgeRouting(nodes.value, graphEdges)
    ? routeGraphEdges(nodes.value, graphEdges, { rerouteEdgeIds })
    : graphEdges;
  edges.value = nextEdges;
  cacheEdgeHandles(nextEdges);
  cacheLocalEdgeRoutes(nextEdges);
};

const rebuildVisibleEdgesWithOptions = (options: {
  rerouteEdgeIds?: Set<string>;
  rerouteAll?: boolean;
} = {}) => {
  const nodeIds = new Set(nodes.value.map(node => String(node.id)));
  const nodeLookup = new Map(nodes.value.map(node => [String(node.id), node]));
  const graphEdges = sourceEdges.value
    .filter(edge => nodeIds.has(noteKey(edge.source_id)) && nodeIds.has(noteKey(edge.target_id)))
    .map(edge => buildGraphEdge(edge, {
      nodeLookup,
      preferDynamicHandles: true
    }));

  const nextEdges = shouldUseDetailedEdgeRouting(nodes.value, graphEdges)
    ? routeGraphEdges(nodes.value, graphEdges, options)
    : graphEdges;
  edges.value = nextEdges;
  cacheEdgeHandles(nextEdges);
  cacheLocalEdgeRoutes(nextEdges);
};

const getAffectedEdgeIdsForNodes = (nodeIds: Iterable<string>) => {
  const draggedNodeIds = new Set(Array.from(nodeIds, nodeId => String(nodeId)));
  return new Set(
    sourceEdges.value
      .filter(edge => draggedNodeIds.has(noteKey(edge.source_id)) || draggedNodeIds.has(noteKey(edge.target_id)))
      .map(edge => String(edge.id))
  );
};

const flushScheduledDragReroute = () => {
  if (dragRerouteFrame !== null) {
    window.cancelAnimationFrame(dragRerouteFrame);
    dragRerouteFrame = null;
  }

  if (isRefreshing.value || isGraphUpdating.value) {
    pendingDragEdgeIds = new Set<string>();
    return;
  }

  const rerouteEdgeIds = pendingDragEdgeIds;
  pendingDragEdgeIds = new Set<string>();

  if (rerouteEdgeIds.size === 0) {
    return;
  }

  rerouteVisibleEdgeSubset(rerouteEdgeIds);
};

const scheduleDragReroute = (edgeIds?: Set<string>) => {
  if (edgeIds) {
    edgeIds.forEach(edgeId => pendingDragEdgeIds.add(edgeId));
  }

  if (dragRerouteFrame !== null) {
    return;
  }

  dragRerouteFrame = window.requestAnimationFrame(() => {
    flushScheduledDragReroute();
  });
};

const getGraphDataForRender = () => {
  const filteredNotes = isGlobalGraph.value
    ? applyNoteProgramChannelLocally(sourceNotes.value, viewProgram.value)
    : sourceNotes.value;
  const visibleNodeIds = new Set(filteredNotes.map(note => noteKey(note.id)));
  const filteredEdges = sourceEdges.value.filter(edge =>
    visibleNodeIds.has(noteKey(edge.source_id)) && visibleNodeIds.has(noteKey(edge.target_id))
  );

  return {
    filteredNotes,
    filteredEdges,
    visibleNodeIds
  };
};

const shouldAutoRelayoutGraph = () => {
  const noteCount = sourceNotes.value.length;
  const edgeCount = sourceEdges.value.length;
  return noteCount <= AUTO_LAYOUT_NODE_LIMIT && edgeCount <= AUTO_LAYOUT_EDGE_LIMIT;
};

const getFallbackNodePosition = (index: number) => {
  const cachedPositions = Object.values(nodePositionCache.value);
  if (cachedPositions.length > 0) {
    const xs = cachedPositions.map(position => position.x);
    const ys = cachedPositions.map(position => position.y);
    return {
      x: Math.max(...xs) + 180 + (index % 3) * 30,
      y: Math.min(...ys) + (index % 4) * 70
    };
  }

  return {
    x: (index % 5) * 220,
    y: Math.floor(index / 5) * 120
  };
};

const buildGraphNodeData = (note: NoteNode) => ({
  title: note.title,
  weight: note.weight,
  node_type: note.node_type,
  note_types: note.note_types,
  primary_category: note.primary_category,
  note_categories: note.note_categories,
  note_form: note.note_form,
  note_kind: note.note_kind,
  node_status: note.node_status,
  lifecycle_stage: note.lifecycle_stage,
  color: note.color,
  weight_mode: note.weight_mode,
  completion_progress_expr: note.completion_progress_expr,
  completion_progress: note.completion_progress,
});

const buildGraphNode = (note: NoteNode, index: number, useCachedPosition: boolean) => {
  const key = noteKey(note.id);
  const cachedPosition = useCachedPosition ? nodePositionCache.value[key] : null;
  return {
    id: key,
    label: note.title || 'Untitled',
    position: cachedPosition ? { ...cachedPosition } : getFallbackNodePosition(index),
    data: buildGraphNodeData(note),
    type: 'custom'
  };
};

const applyGraphFilters = async (force: boolean = false, relayout: boolean = false) => {
  if (!force && isRefreshing.value) return;
  if (isGraphUpdating.value) {
    graphFilterQueued = true;
    graphRelayoutQueued = graphRelayoutQueued || relayout;
    return;
  }
  isGraphUpdating.value = true;
  suppressNodePositionWatch = true;
  try {
    const { filteredNotes, filteredEdges, visibleNodeIds } = getGraphDataForRender();

    let nextNodes: any[] = [];
    let nextEdges: any[] = [];

    if (relayout) {
      const graphEdges = filteredEdges.map(edge => buildGraphEdge(edge, {
        includeLocalRoute: false
      }));
      const graphNodes = filteredNotes.map(note => buildGraphNode(note, 0, false));
      const layoutSeedNodes = graphNodes.map(node => ({
        ...node,
        position: { x: 0, y: 0 }
      }));
      const layouted = await useLayout(layoutSeedNodes, graphEdges);
      nextNodes = layouted.nodes;
      const finalNodeIds = new Set(nextNodes.map(node => String(node.id)));
      clearLocalEdgeRoutes(graphEdges.map(edge => String(edge.id)));
      const layoutedEdges = layouted.edges.filter(edge =>
        finalNodeIds.has(String(edge.source)) && finalNodeIds.has(String(edge.target))
      );
      nextEdges = withRoutePointsFromElk(layoutedEdges);
    } else {
      nextNodes = filteredNotes.map((note, index) => buildGraphNode(note, index, true));
      const nodeLookup = new Map(nextNodes.map(node => [String(node.id), node]));
      const graphEdges = filteredEdges.map(edge => buildGraphEdge(edge, {
        nodeLookup,
        preferDynamicHandles: true
      }));
      const finalNodeIds = new Set(nextNodes.map(node => String(node.id)));
      const visibleGraphEdges = graphEdges.filter(edge =>
        finalNodeIds.has(String(edge.source)) && finalNodeIds.has(String(edge.target))
      );
      nextEdges = shouldUseDetailedEdgeRouting(nextNodes, visibleGraphEdges)
        ? routeGraphEdges(nextNodes, visibleGraphEdges)
        : visibleGraphEdges;
    }

    const detailedRenderRefresh = relayout || shouldUseDetailedEdgeRouting(nextNodes, nextEdges);
    edges.value = [];
    nodes.value = nextNodes;
    await refreshNodeInternals(nextNodes.map(node => String(node.id)));
    edges.value = nextEdges;
    if (detailedRenderRefresh) {
      await refreshNodeInternals(nextNodes.map(node => String(node.id)));
      refreshRenderedEdges(nextEdges);
    }
    cacheNodePositions(nextNodes);
    cacheEdgeHandles(nextEdges);
    cacheEdgeRoutes(nextEdges);
    cacheLocalEdgeRoutes(nextEdges);

    if (currentNoteId.value && !visibleNodeIds.has(currentNoteId.value)) {
      currentNoteId.value = '';
    }
  } finally {
    suppressNodePositionWatch = false;
    isGraphUpdating.value = false;
    if (graphFilterQueued) {
      const queuedRelayout = graphRelayoutQueued;
      graphFilterQueued = false;
      graphRelayoutQueued = false;
      void applyGraphFilters(true, queuedRelayout);
    }
  }
};

const syncEdgesFromStore = async () => {
    if (isRefreshing.value) return;
    if (isGraphUpdating.value) {
        await nextTick();
        if (isGraphUpdating.value) return;
    }
    rebuildVisibleEdges();
};

const selectNote = async (noteId: string) => {
  currentNoteId.value = noteId;
};

const handleNoteUpdate = (note: NoteNode) => {
    // Update graph node data
    const key = noteKey(note.id);
    const node = nodes.value.find(n => n.id === key);
    if (node) {
        node.label = note.title;
        node.data = {
          ...(node.data || {}),
          ...buildGraphNodeData(note)
        };
        void refreshNodeInternals([key]).then(() => {
          const affectedEdgeIds = getAffectedEdgeIdsForNodes([key]);
          if (affectedEdgeIds.size > 0) {
            rebuildVisibleEdgesWithOptions({ rerouteEdgeIds: affectedEdgeIds });
          }
        });
    }
};

const handleNoteCreate = (note: NoteNode) => {
    const key = noteKey(note.id);
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
      id: key,
      label: note.title,
      position: pos,
      data: buildGraphNodeData(note),
      type: 'custom'
    };
    nodePositionCache.value = {
      ...nodePositionCache.value,
      [key]: { ...pos }
    };
    nodes.value.push(newNode);
    noteStore.addNoteToTab(props.tabId, note.id);
    selectNote(key);
};

const handleNoteDelete = (noteId: string) => {
    nodes.value = nodes.value.filter(n => n.id !== noteId);
    const { [noteId]: _removedNodePosition, ...restNodePositions } = nodePositionCache.value;
    nodePositionCache.value = restNodePositions;
    if (currentNoteId.value === noteId) {
        currentNoteId.value = '';
    }
};

watch(sourceEdgesVersion, async () => {
    if (!isActive.value) {
        inactiveGraphRefreshPending = true;
        return;
    }
    await syncEdgesFromStore();
});

watch(sourceNotesVersion, async () => {
    if (!isActive.value) {
        inactiveGraphRefreshPending = true;
        return;
    }
    if (!isRefreshing.value) {
        await applyGraphFilters(true, false);
    }
});

watch(viewProgram, async (value) => {
    noteStore.updateTabViewState(props.tabId, {
        viewProgram: normalizeNoteProgramChannel(value)
    });

    if (isGlobalGraph.value && isActive.value && !isRefreshing.value) {
        if (graphViewNeedsCustomFields.value && !currentGlobalGraphIncludesCustomFields()) {
            void refreshGraph(getAppliedDataProgram(), false);
            return;
        }
        scheduleGraphFilterApply();
    } else if (isGlobalGraph.value) {
        inactiveGraphRefreshPending = true;
    }
}, { deep: true });

onMounted(async () => {
    if (canUseCachedGlobalGraph()) {
        const shouldDeferRelayout = shouldDeferAutoRelayout(hasCachedNodePositions());
        await applyGraphFilters(true, false);
        if (shouldDeferRelayout) {
          scheduleDeferredInitialRelayout();
        }
        if (!isCachedGlobalGraphFresh()) {
          void nextTick(() => refreshGraph(getAppliedDataProgram(), false, { background: true }));
        }
        return;
    }
    await refreshGraph();
});

watch(isActive, async (active) => {
    if (!active) return;
    if (canUseCachedGlobalGraph()) {
        const shouldDeferRelayout = inactiveGraphRefreshPending && shouldDeferAutoRelayout(hasCachedNodePositions());
        await applyGraphFilters(true, false);
        inactiveGraphRefreshPending = false;
        if (shouldDeferRelayout) {
            scheduleDeferredInitialRelayout();
        }
        if (!isCachedGlobalGraphFresh()) {
            void nextTick(() => refreshGraph(getAppliedDataProgram(), false, { background: true }));
        }
        return;
    }
    inactiveGraphRefreshPending = false;
    await refreshGraph();
});

onUnmounted(() => {
    if (graphFilterTimer) {
        clearTimeout(graphFilterTimer);
        graphFilterTimer = null;
    }
    clearDeferredInitialRelayout();
    if (dragRerouteFrame !== null) {
        window.cancelAnimationFrame(dragRerouteFrame);
        dragRerouteFrame = null;
    }
});

const refreshGraph = async (
  program = getAppliedDataProgram(),
  persist: boolean = false,
  options: { background?: boolean } = {}
) => {
  if (isRefreshing.value) return;
  isRefreshing.value = true;
  try {
    let deferredStoreRefresh = false;
    const hadCachedNodePositions = hasCachedNodePositions();
    if (isGlobalGraph.value) {
      const normalizedProgram = normalizeNoteProgramChannel(program);
      const request = buildGlobalGraphRequest(normalizedProgram);
      if (options.background && canUseCachedGlobalGraph(normalizedProgram)) {
        void noteStore.queryNoteProgramForTab(props.tabId, request);
        deferredStoreRefresh = true;
      } else {
        await noteStore.queryNoteProgramForTab(props.tabId, request);
      }
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

    if (!deferredStoreRefresh) {
      await applyGraphFilters(true, false);
      if (shouldDeferAutoRelayout(hadCachedNodePositions)) {
        scheduleDeferredInitialRelayout();
      }
    }
  } finally {
      isRefreshing.value = false;
  }
};

const relayoutGraph = async () => {
  await applyGraphFilters(true, true);
};

onNodeDrag(({ node, nodes: draggedNodes }) => {
  cacheDraggedNodePositions(draggedNodes?.length ? draggedNodes : [node]);
  const affectedEdgeIds = getAffectedEdgeIdsForNodes(
    (draggedNodes?.length ? draggedNodes : [node]).map(item => String(item.id))
  );
  if (affectedEdgeIds.size === 0) return;
  scheduleDragReroute(affectedEdgeIds);
});

onNodeDragStop(({ node, nodes: draggedNodes }) => {
  cacheDraggedNodePositions(draggedNodes?.length ? draggedNodes : [node]);
  const affectedEdgeIds = getAffectedEdgeIdsForNodes(
    (draggedNodes?.length ? draggedNodes : [node]).map(item => String(item.id))
  );
  scheduleDragReroute(affectedEdgeIds);
  flushScheduledDragReroute();
});

// Handle Connection
const onConnect = async (params: Connection) => {
    if (!ensureNoteWritable()) return;
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
    rebuildVisibleEdges(new Set([String(persistedEdge.id)]));
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
    if (!ensureNoteWritable()) return;
    
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
        removeEdgeCaches([String(previousEdge.id)]);
        selectedEdgeId.value = null;
        ElMessage.success('边已删除');
    }
};

// Handle Edge Delete (Backspace/Delete key)
onEdgesChange((changes) => {
    changes.forEach((change) => {
        if (change.type === 'remove') {
            const edge = edges.value.find(e => e.id === change.id);
            if (edge) {
                void noteStore.deleteEdge(edge.source, edge.target).then(success => {
                    if (success) {
                        removeEdgeCaches([String(edge.id)]);
                        return;
                    }

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

const ensureNoteWritable = () => {
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
  if (!ensureNoteWritable()) return;
  
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
    const key = noteKey(newNote.id);
    // Add to graph
    const newNode = {
      id: key,
      label: newNote.title,
      position: pos,
      data: buildGraphNodeData(newNote),
      type: 'custom'
    };
    nodePositionCache.value = {
      ...nodePositionCache.value,
      [key]: { ...pos }
    };
    nodes.value.push(newNode);
    noteStore.addNoteToTab(props.tabId, newNote.id);
    
    selectNote(key);
  }
};

watch(
  () => nodes.value.map(node => `${node.id}:${Math.round(node.position?.x ?? 0)}:${Math.round(node.position?.y ?? 0)}`).join('|'),
  () => {
    if (suppressNodePositionWatch) return;
    cacheNodePositions();
  },
  { flush: 'post' }
);

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
