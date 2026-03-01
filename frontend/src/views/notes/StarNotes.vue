<template>
  <div class="task-manager-layout">
    <!-- Top: Filter Panel -->
    <div class="filter-section">
      <div class="scope-info">
        <span class="label">当前数据范围:</span>
        <div v-if="hasActiveFilter" class="scope-tags">
            <el-tag v-if="filters.createdRange.length" closable @close="clearCreatedFilter" size="small">
                起始: {{ formatDateSimple(filters.createdRange[0]) }} - {{ formatDateSimple(filters.createdRange[1]) }}
            </el-tag>
            <el-tag v-if="filters.updatedRange.length" closable @close="clearUpdatedFilter" size="small" type="warning">
                更新: {{ formatDateSimple(filters.updatedRange[0]) }} - {{ formatDateSimple(filters.updatedRange[1]) }}
            </el-tag>
        </div>
        <span v-else class="text-gray">全部数据 (未筛选)</span>
      </div>
      
      <div class="actions">
          <el-button :icon="Calendar" @click="router.push('/notes/calendar')">日历视图</el-button>
          <el-button type="primary" :icon="Filter" @click="openFilterDialog">筛选加载范围</el-button>
          <el-button :icon="Refresh" @click="refreshGraph">刷新</el-button>
      </div>
    </div>

    <!-- Filter Dialog -->
    <el-dialog v-model="showFilterDialog" title="数据加载设置 (Data Scope)" width="550px">
        <div class="filter-dialog-content">
            <el-alert
                title="什么是数据加载范围？"
                type="info"
                :closable="false"
                show-icon
                style="margin-bottom: 20px;"
            >
                <p style="font-size: 12px; line-height: 1.6; margin: 5px 0 0 0;">
                    这里决定了<b>从数据库下载到浏览器内存</b>的笔记总量。
                    范围越大，加载越慢，但您可以在后续的视图中切换查看。
                    建议仅加载您当前关注的时间段。
                </p>
            </el-alert>

            <el-form label-position="top">
                <el-form-item label="起始时间范围 (Start Time)">
                    <el-date-picker
                        v-model="tempFilters.createdRange"
                        type="datetimerange"
                        range-separator="至"
                        start-placeholder="开始日期"
                        end-placeholder="结束日期"
                        value-format="x"
                        :shortcuts="shortcuts"
                        style="width: 100%"
                    />
                    <div class="form-tip">匹配笔记的“起始时间”属性</div>
                </el-form-item>
                <el-form-item label="更新时间范围 (Updated Time)">
                    <el-date-picker
                        v-model="tempFilters.updatedRange"
                        type="datetimerange"
                        range-separator="至"
                        start-placeholder="开始日期"
                        end-placeholder="结束日期"
                        value-format="x"
                        :shortcuts="shortcuts"
                        style="width: 100%"
                    />
                    <div class="form-tip">匹配笔记最后一次编辑的时间</div>
                </el-form-item>
            </el-form>
        </div>
        <template #footer>
            <span class="dialog-footer">
                <el-button @click="showFilterDialog = false">取消</el-button>
                <el-button type="primary" @click="applyFiltersAndLoad">执行加载 (Fetch)</el-button>
            </span>
        </template>
    </el-dialog>

    <!-- Sub-Filter: Range Sliders -->
    <div class="slider-section" v-if="nodes.length > 0">
        <div class="slider-header">
            <span class="title">视图实时聚焦 (View Filtering)</span>
            <el-tooltip content="仅在已加载的任务中快速切换可见性，不请求后端" placement="top">
                <el-icon><QuestionFilled /></el-icon>
            </el-tooltip>
        </div>
        <div class="slider-item">
            <span class="label">起始时间范围</span>
            <div class="slider-container">
                <div class="density-bar">
                    <div v-for="(opacity, idx) in densityData.created" :key="idx" class="density-bin"
                        :style="{ opacity: opacity > 0 ? Math.max(0.3, opacity) : 0, backgroundColor: opacity > 0 ? (idx >= futureBinIndex ? '#e6a23c' : '#409eff') : 'transparent' }">
                    </div>
                </div>
                <div v-if="nowPositionPercent >= 0 && nowPositionPercent <= 100" class="now-marker" :style="{ left: nowPositionPercent + '%' }"></div>
                <el-slider 
                    v-model="sliderFilters.created" 
                    range 
                    :min="startTimeBounds.min" 
                    :max="startTimeBounds.max"
                    :step="startSliderStep" 
                    :format-tooltip="formatDateSimple"
                />
                <div class="time-axis">
                    <div v-for="tick in startTimeTicks" :key="tick.label" class="tick" :style="{ left: tick.percent + '%' }">
                        <span class="tick-label">{{ tick.label }}</span>
                    </div>
                </div>
            </div>
        </div>
        <div class="slider-item">
            <span class="label">更新时间范围</span>
            <div class="slider-container">
                <div class="density-bar">
                    <div v-for="(opacity, idx) in densityData.updated" :key="idx" class="density-bin"
                        :style="{ opacity: opacity > 0 ? Math.max(0.3, opacity) : 0, backgroundColor: opacity > 0 ? '#409eff' : 'transparent' }">
                    </div>
                </div>
                <!-- Updated time usually doesn't need future marker, but can keep if desired. Removed for clarity as updated is usually past. -->
                <el-slider 
                    v-model="sliderFilters.updated" 
                    range 
                    :min="updatedTimeBounds.min" 
                    :max="updatedTimeBounds.max"
                    :step="updatedSliderStep"
                    :format-tooltip="formatDateSimple"
                />
                <div class="time-axis">
                    <div v-for="tick in updatedTimeTicks" :key="tick.label" class="tick" :style="{ left: tick.percent + '%' }">
                        <span class="tick-label">{{ tick.label }}</span>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Middle: Graph -->
    <div class="graph-section" :style="{ height: graphHeight + 'px' }">
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
        @node-click="onNodeClick"
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
    </div>
    
    <!-- Bottom: Editor -->
    <div class="editor-section" :class="{ 'is-collapsed': !currentNote }">
      <div v-if="currentNote" class="editor-wrapper" v-loading="isFetchingContent">
        <div v-if="!isFetchingContent">
          <div class="editor-header">
            <!-- Row 1: Title and Delete -->
            <div class="header-row-primary">
                <el-input 
                  v-model="currentNote.title" 
                  placeholder="节点标题" 
                  class="title-input" 
                  @input="onTitleChange" 
                />
                <el-button type="danger" plain text circle :icon="Delete" @click="deleteCurrentNote" title="删除节点"></el-button>
            </div>
            
            <!-- Row 2: Meta Info and Save Status -->
            <div class="header-row-secondary">
                <div class="meta-group">
                    <span class="time-tag">
                      <el-icon><Calendar /></el-icon> 起始: 
                      <el-date-picker
                          v-model="currentNote.start_at"
                          type="datetime"
                          placeholder="选择起始时间"
                          size="small"
                          :clearable="false"
                          format="YYYY/MM/DD HH:mm:ss"
                          value-format="x"
                          @change="onStartTimeChange"
                          style="width: 180px; margin-left: 5px;"
                      />
                    </span>
                    <span class="time-tag">
                      <el-icon><Clock /></el-icon> 更新: {{ formatDateDetailed(currentNote.updated_at) }}
                    </span>
                </div>
                <div class="save-status">
                    <span v-if="saveStatus === 'saved'" class="status-saved"><el-icon><Check /></el-icon> 已保存</span>
                    <span v-else-if="saveStatus === 'saving'" class="status-saving"><el-icon class="is-loading"><Loading /></el-icon> 保存中...</span>
                    <span v-else class="status-unsaved">未保存</span>
                </div>
            </div>

            <!-- Row 3: Weight and Node Type Configuration -->
            <div class="header-row-tertiary">
                <div class="weight-control">
                  <span class="label">权重:</span>
                  <el-input-number 
                      v-model="currentNote.weight" 
                      :min="1" 
                      :max="10000" 
                      :step="10" 
                      size="small"
                      :controls="false"
                      @change="onWeightChange"
                      @blur="onWeightBlur"
                  />
                </div>
                <div class="status-control">
                  <span class="label">节点类型:</span>
                  <el-select 
                    v-model="currentNote.node_type" 
                    size="small" 
                    placeholder="选择类型"
                    clearable
                    @change="onNodeTypeChange"
                    style="width: 120px;"
                  >
                    <el-option label="普通 (None)" :value="null" />
                    <el-option label="项目 (Project)" value="project" />
                    <el-option label="模块 (Module)" value="module" />
                    <el-option label="备忘 (Memo)" value="memo" />
                    <el-option label="待办 (Todo)" value="todo" />
                    <el-option label="进行中 (Doing)" value="doing" />
                    <el-option label="预完成 (Pre-done)" value="pre-done" />
                    <el-option label="完成 (Done)" value="done" />
                    <el-option label="删除/放弃 (Delete)" value="delete" />
                    <el-option label="缺陷 (Bug)" value="bug" />
                  </el-select>
                  <el-tooltip effect="light" placement="top">
                    <template #content>
                      <div style="line-height: 1.6; max-width: 300px;">
                        <b>节点类型说明:</b><br/>
                        - <b>项目</b>: 长期性工作，非具体任务容器。<br/>
                        - <b>模块</b>: 项目的组成部分，中间层级。<br/>
                        - <b>待办</b>: 计划要做的具体事项。<br/>
                        - <b>进行中</b>: 正在处理的任务。<br/>
                        - <b>预完成</b>: 已初步完成，待验证或确认。<br/>
                        - <b>完成</b>: 任务正式结束。<br/>
                        - <b>删除/放弃</b>: 原定计划取消或放弃，加删除线。<br/>
                        - <b>缺陷</b>: 需要修复的问题。<br/>
                        - <b>备忘</b>: 记录信息，非任务。
                      </div>
                    </template>
                    <el-icon class="help-icon"><QuestionFilled /></el-icon>
                  </el-tooltip>
                </div>
                <div class="history-toggle">
                  <el-button 
                    size="small" 
                    :type="showHistory ? 'primary' : ''" 
                    :icon="List" 
                    @click="showHistory = !showHistory"
                  >
                    操作日志
                  </el-button>
                </div>
            </div>
            
            <!-- History Section -->
            <div v-if="showHistory" class="history-panel">
              <div v-if="!currentNote.history || currentNote.history.length === 0" class="history-empty">
                暂无操作记录
              </div>
              <div v-else class="history-list">
                <div v-for="(entry, index) in sortedHistory" :key="index" class="history-item">
                  <span class="history-time">{{ formatDateDetailed(entry.ts * 1000) }}</span>
                  <span class="history-content">
                    <el-tag size="small" :type="getFieldTagType(entry.f)" class="field-tag">{{ getFieldName(entry.f) }}</el-tag>
                    <span class="history-value">{{ formatHistoryValue(entry.f, entry.v) }}</span>
                  </span>
                </div>
              </div>
            </div>
          </div>
          <NoteEditor :key="currentNote.id" v-model="currentNote.content" @change="onContentChange" />
        </div>
      </div>
      <div v-else class="empty-state">
        <el-empty description="请在上方图表中选择一个节点" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router';
import { useUserStore } from '@/store/userStore';
import { markRaw, ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { Plus, Clock, Check, Loading, Refresh, Delete, Calendar, Filter, QuestionFilled, List } from '@element-plus/icons-vue';
import NoteEditor from '@/components/NoteEditor.vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { useNoteStore, type NoteNode } from '@/api/notes';
import { VueFlow, useVueFlow, Connection, MarkerType } from '@vue-flow/core';
import { Background } from '@vue-flow/background';
import { Controls } from '@vue-flow/controls';
import CustomNode from '@/components/CustomNode.vue';
import ElkEdge from '@/components/ElkEdge.vue';
import { useLayout } from '@/utils/useLayout';

const nodeTypes = {
  custom: markRaw(CustomNode),
};

const edgeTypes = {
  elk: markRaw(ElkEdge),
};
import '@vue-flow/core/dist/style.css';
import '@vue-flow/core/dist/theme-default.css';
import '@vue-flow/controls/dist/style.css';

const router = useRouter();
const userStore = useUserStore();
const noteStore = useNoteStore();

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
    
    // Total estimated height of fixed headers/sliders
    // filter-section: 60px, slider-section: ~120px, margins/others: ~40px
    const reservedHeight = 220; 
    const availableHeight = vh - reservedHeight;

    if (isPortrait) {
        // Vertical screen: give more space to canvas (e.g. 70% of available)
        return Math.max(400, Math.floor(availableHeight * 0.7));
    } else {
        // Horizontal screen: 50%
        return Math.max(300, Math.floor(availableHeight * 0.5));
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
    const newHeight = Math.max(200, startHeight.value + delta); // Min 200px
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
const { 
  onEdgesChange, 
  applyEdgeChanges, 
  onEdgeClick, 
  onPaneClick,
} = useVueFlow();

const selectedEdgeId = ref<string | null>(null);

// Editor state
const currentNoteId = ref<string>('');
// 存储原始内容基准值（包含 title 和 content）
const originalData = ref(new Map<string, { title: string, content: string, weight: number, start_at: number, node_type: string | null }>());
const saveStatus = ref<'saved' | 'saving' | 'unsaved'>('saved');
const isFetchingContent = ref(false);
const showHistory = ref(false);
let saveTimeout: any = null;

const sortedHistory = computed(() => {
    if (!currentNote.value || !currentNote.value.history) return [];
    // Sort by timestamp descending (newest first)
    return [...currentNote.value.history].sort((a, b) => b.ts - a.ts);
});

const getFieldName = (f: string) => {
    const map: Record<string, string> = {
        'n': '类型',
        's': '类型', // Backward compatibility
        't': '标题',
        'w': '权重',
        'c': '内容'
    };
    return map[f] || f;
};

const getFieldTagType = (f: string) => {
    const map: Record<string, string> = {
        'n': 'warning',
        's': 'warning', // Backward compatibility
        't': '',
        'w': 'success',
        'c': 'info'
    };
    return map[f] || '';
};

const formatHistoryValue = (f: string, v: any) => {
    if (f === 'n' || f === 's') return getNodeTypeLabel(v);
    if (f === 'c') return `${v} 字`;
    return v;
};

// Applied Filters (The "Scope")
const filters = ref({
    createdRange: [] as number[],
    updatedRange: [] as number[],
});

// Temporary Filters (For Dialog)
const tempFilters = ref({
    createdRange: [] as number[],
    updatedRange: [] as number[],
});
const showFilterDialog = ref(false);

const hasActiveFilter = computed(() => {
    return filters.value.createdRange.length > 0 || filters.value.updatedRange.length > 0;
});

const openFilterDialog = () => {
    tempFilters.value.createdRange = [...filters.value.createdRange];
    tempFilters.value.updatedRange = [...filters.value.updatedRange];
    showFilterDialog.value = true;
};

const applyFiltersAndLoad = () => {
    filters.value.createdRange = [...tempFilters.value.createdRange];
    filters.value.updatedRange = [...tempFilters.value.updatedRange];
    showFilterDialog.value = false;
    refreshGraph();
};

const clearCreatedFilter = () => {
    filters.value.createdRange = [];
    refreshGraph();
};

const clearUpdatedFilter = () => {
    filters.value.updatedRange = [];
    refreshGraph();
};

// Slider States
const startTimeBounds = computed(() => {
    if (noteStore.notes.length === 0) return { min: 0, max: 0 };
    const times = noteStore.notes.map(n => n.start_at);
    return {
        min: Math.min(...times),
        max: Math.max(...times)
    };
});

const updatedTimeBounds = computed(() => {
    if (noteStore.notes.length === 0) return { min: 0, max: 0 };
    const times = noteStore.notes.map(n => n.updated_at);
    return {
        min: Math.min(...times),
        max: Math.max(...times)
    };
});

const sliderFilters = ref({
    created: [0, 0] as number[],
    updated: [0, 0] as number[]
});

// Watch bounds to reset sliders if they become invalid or on initial load
// Logic moved to after onSliderChange definition to support reactivity

const densityData = computed(() => {
    const bins = 100;
    const result = {
        created: new Array(bins).fill(0),
        updated: new Array(bins).fill(0)
    };
    
    // Created Distribution (start_at)
    const sMin = startTimeBounds.value.min;
    const sMax = startTimeBounds.value.max;
    const sRange = sMax - sMin;
    
    if (sRange > 0) {
        const cCounts = new Array(bins).fill(0);
        noteStore.notes.forEach(note => {
            const progress = (note.start_at - sMin) / sRange;
            const idx = Math.min(bins - 1, Math.floor(progress * bins));
            if (idx >= 0 && idx < bins) cCounts[idx]++;
        });
        const cMax = Math.max(...cCounts, 1);
        result.created = cCounts.map(c => c / cMax);
    }

    // Updated Distribution (updated_at)
    const uMin = updatedTimeBounds.value.min;
    const uMax = updatedTimeBounds.value.max;
    const uRange = uMax - uMin;

    if (uRange > 0) {
        const uCounts = new Array(bins).fill(0);
        noteStore.notes.forEach(note => {
            const progress = (note.updated_at - uMin) / uRange;
            const idx = Math.min(bins - 1, Math.floor(progress * bins));
            if (idx >= 0 && idx < bins) uCounts[idx]++;
        });
        const uMax = Math.max(...uCounts, 1);
        result.updated = uCounts.map(u => u / uMax);
    }
    
    return result;
});

const startSliderStep = computed(() => {
    const range = startTimeBounds.value.max - startTimeBounds.value.min;
    return Math.max(1, Math.floor(range / 100));
});

const updatedSliderStep = computed(() => {
    const range = updatedTimeBounds.value.max - updatedTimeBounds.value.min;
    return Math.max(1, Math.floor(range / 100));
});

const nowPositionPercent = computed(() => {
    const min = startTimeBounds.value.min;
    const max = startTimeBounds.value.max;
    if (max <= min) return -1;
    const now = Date.now();
    const percent = ((now - min) / (max - min)) * 100;
    return percent;
});

const futureBinIndex = computed(() => {
    const min = startTimeBounds.value.min;
    const max = startTimeBounds.value.max;
    if (max <= min) return -1;
    const now = Date.now();
    if (now < min) return 0;
    if (now > max) return 100;
    const progress = (now - min) / (max - min);
    return Math.floor(progress * 100);
});

// Time axis labels logic
const getTimeAxisTicks = (min: number, max: number) => {
    if (min >= max) return [];
    
    const range = max - min;
    const ticks: { percent: number, label: string }[] = [];
    
    // Choose appropriate interval based on range
    let interval: number;
    let type: 'time' | 'date' | 'month' | 'year';
    
    const hour = 3600 * 1000;
    const day = 24 * hour;
    const month = 30 * day;
    const year = 365 * day;
    
    if (range < 24 * hour) {
        // Less than 24h, show every 2-4 hours
        interval = range < 6 * hour ? hour : 4 * hour;
        type = 'time';
    } else if (range < 14 * day) {
        // Less than 2 weeks, show every day
        interval = day;
        type = 'date';
    } else if (range < 3 * month) {
        // Less than 3 months, show every week
        interval = 7 * day;
        type = 'date';
    } else if (range < 2 * year) {
        // Less than 2 years, show every month
        interval = month;
        type = 'month';
    } else {
        interval = year;
        type = 'year';
    }
    
    const formatTick = (date: Date, type: string) => {
        const y = date.getFullYear();
        const m = date.getMonth() + 1;
        const d = date.getDate();
        const hh = String(date.getHours()).padStart(2, '0');
        const mm = String(date.getMinutes()).padStart(2, '0');
        const dayOfWeek = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][date.getDay()];
        
        if (type === 'time') return `${hh}:${mm}`;
        if (type === 'date') return `${m}/${d}${dayOfWeek}`;
        if (type === 'month') return `${y}/${m}`;
        return `${y}`;
    };
    
    // Adjust start of ticks to be a nice round number (e.g., start of day)
    const startDate = new Date(min);
    if (interval >= month) {
        startDate.setDate(1);
        startDate.setHours(0, 0, 0, 0);
    } else if (interval === 7 * day) {
        // Align to Monday
        const day = startDate.getDay();
        const diff = startDate.getDate() - day + (day === 0 ? -6 : 1);
        startDate.setDate(diff);
        startDate.setHours(0, 0, 0, 0);
    } else if (interval >= day) {
        startDate.setHours(0, 0, 0, 0);
    } else if (interval >= hour) {
        startDate.setMinutes(0, 0, 0);
    }
    
    let current = startDate.getTime();
    let safetyCounter = 0;
    while (current <= max && safetyCounter < 50) {
        if (current >= min) {
            const percent = ((current - min) / range) * 100;
            ticks.push({
                percent,
                label: formatTick(new Date(current), type)
            });
        }
        
        // Month/Year intervals need special handling for uneven lengths
        if (type === 'month') {
            const d = new Date(current);
            d.setMonth(d.getMonth() + 1);
            current = d.getTime();
        } else if (type === 'year') {
            const d = new Date(current);
            d.setFullYear(d.getFullYear() + 1);
            current = d.getTime();
        } else {
            current += interval;
        }
        safetyCounter++;
    }
    
    return ticks;
};

const startTimeTicks = computed(() => getTimeAxisTicks(startTimeBounds.value.min, startTimeBounds.value.max));
const updatedTimeTicks = computed(() => getTimeAxisTicks(updatedTimeBounds.value.min, updatedTimeBounds.value.max));

const shortcuts = [
    { text: '最近一周', value: () => { const end = new Date(); const start = new Date(); start.setTime(start.getTime() - 3600 * 1000 * 24 * 7); return [start, end]; } },
    { text: '最近一月', value: () => { const end = new Date(); const start = new Date(); start.setTime(start.getTime() - 3600 * 1000 * 24 * 30); return [start, end]; } },
    { text: '最近三月', value: () => { const end = new Date(); const start = new Date(); start.setTime(start.getTime() - 3600 * 1000 * 24 * 90); return [start, end]; } },
];

// Real-time slider filter with debounce
const onSliderChange = (() => {
    let timeout: any = null;
    return () => {
        if (timeout) clearTimeout(timeout);
        timeout = setTimeout(() => {
            applyFilters();
        }, 100); // 100ms debounce
    };
})();

// Watch bounds to update slider ranges. If user was at the edge, keep them at the edge.
// This addresses the requirement: "右端点应该要自动更新到最右边最新位置"
watch(startTimeBounds, (newBounds, oldBounds) => {
    const [currStart, currEnd] = sliderFilters.value.created;
    
    // Case 1: Initial load or default state
    if (currStart === 0 && currEnd === 0) {
        sliderFilters.value.created = [newBounds.min, newBounds.max];
        return;
    }

    let nextStart = currStart;
    let nextEnd = currEnd;

    // Case 2: Stick to max. If the handle was at the old maximum, keep it at the new maximum.
    if (oldBounds && currEnd === oldBounds.max) {
        nextEnd = newBounds.max;
    }

    // Case 3: Boundary enforcement
    if (nextEnd > newBounds.max) nextEnd = newBounds.max;
    if (nextStart < newBounds.min) nextStart = newBounds.min;
    if (nextStart > nextEnd) nextStart = nextEnd;

    // Only update if changed to avoid unnecessary triggers
    if (nextStart !== currStart || nextEnd !== currEnd) {
        sliderFilters.value.created = [nextStart, nextEnd];
    }
}, { immediate: true });

watch(updatedTimeBounds, (newBounds, oldBounds) => {
    const [currStart, currEnd] = sliderFilters.value.updated;
    
    if (currStart === 0 && currEnd === 0) {
        sliderFilters.value.updated = [newBounds.min, newBounds.max];
        return;
    }

    let nextStart = currStart;
    let nextEnd = currEnd;

    if (oldBounds && currEnd === oldBounds.max) {
        nextEnd = newBounds.max;
    }

    if (nextEnd > newBounds.max) nextEnd = newBounds.max;
    if (nextStart < newBounds.min) nextStart = newBounds.min;
    if (nextStart > nextEnd) nextStart = nextEnd;

    if (nextStart !== currStart || nextEnd !== currEnd) {
        sliderFilters.value.updated = [nextStart, nextEnd];
    }
}, { immediate: true });

// React to slider changes (both user interaction via v-model and programmatic updates from bounds watchers)
watch(sliderFilters, () => {
    onSliderChange();
}, { deep: true });

const applyFilters = async () => {
    // Filter nodes based on SLIDERS
    const filteredNotes = noteStore.notes.filter(note => {
        let pass = true;
        
        // Check Slider Created Range (mapped to start_at)
        if (sliderFilters.value.created.length === 2) {
             const [start, end] = sliderFilters.value.created;
             if (note.start_at < start || note.start_at > end) {
                 pass = false;
             }
        }
        
        // Check Slider Updated Range
        if (pass && sliderFilters.value.updated.length === 2) {
             const [start, end] = sliderFilters.value.updated;
             if (note.updated_at < start || note.updated_at > end) {
                 pass = false;
             }
        }
        
        return pass;
    });
    
    // Get visible node IDs for edge filtering
    const visibleNodeIds = new Set(filteredNotes.map(n => n.id));
    
    // Filter Edges: Only show edges where BOTH source and target are visible
    const filteredEdges = noteStore.edges.filter(edge => {
        return visibleNodeIds.has(edge.source_id) && visibleNodeIds.has(edge.target_id);
    });
    
    // Update Graph State
    const graphNodes = filteredNotes.map(note => ({
       id: note.id,
       label: note.title || 'Untitled',
       position: { x: 0, y: 0 },
       data: { 
           title: note.title,
           weight: note.weight,
           node_type: note.node_type,
           created_at: note.created_at, // Keep for debug/info if needed
           start_at: note.start_at
       },
       type: 'custom',
    }));
    
    const graphEdges = filteredEdges.map(e => ({
        id: e.id,
        source: e.source_id,
        target: e.target_id,
        label: e.label,
        type: 'elk',
        markerEnd: MarkerType.ArrowClosed,
    }));
    
    // Re-run Layout
    const layouted = await useLayout(graphNodes, graphEdges);
   
    nodes.value = layouted.nodes;
    edges.value = layouted.edges;
    
    // Deselect if current note is hidden
    if (currentNoteId.value && !visibleNodeIds.has(currentNoteId.value)) {
        currentNoteId.value = '';
    }
};

const currentNote = computed(() => {
  // Find note from store (single source of truth for content)
  return noteStore.notes.find(t => t.id === currentNoteId.value);
});

// Sync store changes back to local edges (e.g. after createEdge returns real ID)
watch(() => noteStore.edges, (newStoreEdges) => {
    // Check if lengths are different or if we have temporary IDs
    const hasTempIds = edges.value.some(e => e.id.startsWith('e-'));
    if (newStoreEdges.length !== edges.value.length || hasTempIds) {
        edges.value = newStoreEdges.map(e => ({
            id: e.id,
            source: e.source_id,
            target: e.target_id,
            label: e.label,
            type: 'elk',
            markerEnd: MarkerType.ArrowClosed,
        }));
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

const refreshGraph = async () => {
  if (!userStore.isAuthenticated) {
    // Clear graph if not authenticated
    noteStore.notes = [];
    noteStore.edges = [];
    nodes.value = [];
    edges.value = [];
    return;
  }
  
  // Default Filter Initialization (if none set)
  // Ensure we start with a sane default scope: Updated in last 7 days + future 7 days
  if (filters.value.createdRange.length === 0 && filters.value.updatedRange.length === 0) {
      const now = new Date();
      
      const start = new Date(now);
      start.setDate(start.getDate() - 7);
      start.setHours(0, 0, 0, 0);
      
      const end = new Date(now);
      end.setDate(end.getDate() + 7);
      end.setHours(23, 59, 59, 999);
      
      filters.value.updatedRange = [start.getTime(), end.getTime()];
  }

  const queryParams: any = {};
  
  // Backend Date Picker Filters
  if (filters.value.createdRange && filters.value.createdRange.length === 2) {
      queryParams.created_start = filters.value.createdRange[0];
      queryParams.created_end = filters.value.createdRange[1];
  }
  
  if (filters.value.updatedRange && filters.value.updatedRange.length === 2) {
      queryParams.updated_start = filters.value.updatedRange[0];
      queryParams.updated_end = filters.value.updatedRange[1];
  }

  await noteStore.fetchNotes(queryParams);
  
  // Initialize original contents (only metadata at first)
  noteStore.notes.forEach(task => {
      originalData.value.set(task.id, { 
          title: task.title, 
          content: task.content || '', 
          weight: task.weight,
          start_at: task.start_at,
          node_type: task.node_type || null
      });
  });
  
  // Map Edges
  edges.value = noteStore.edges.map(e => ({
      id: e.id,
      source: e.source_id,
      target: e.target_id,
      label: e.label,
      type: 'elk',
      markerEnd: MarkerType.ArrowClosed,
  }));
  
  // Map Nodes (Initial)
  const initialNodes = noteStore.notes.map(note => ({
       id: note.id,
       label: note.title || 'Untitled',
       position: { x: 0, y: 0 }, // Initial position
       data: { 
           title: note.title,
           weight: note.weight,
           created_at: note.created_at,
           start_at: note.start_at
       },
       type: 'custom',
   }));
   
   // Apply Layout
  const layouted = await useLayout(initialNodes, edges.value);
   
  nodes.value = layouted.nodes;
  edges.value = layouted.edges;
   
  // Initialize filters (The Data Loading Scope)
  // Logic: Default load "updated" within last 7 days and future 7 days (active window)
  // "Created/Start" filter is left empty to include all tasks active in that window regardless of when they started.
  // Logic moved to start of refreshGraph to ensure first fetch respects this.
  
  // Re-run frontend filtering logic on newly fetched data
  applyFilters();
};

const formatDateSimple = (timestamp: number) => {
    return new Date(timestamp).toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    });
};

// Handle Connection
const onConnect = (params: Connection) => {
    if (!checkAuth()) return;
    // 确保连接对象包含箭头样式和具体的句柄 ID
    const edgeParams = {
        ...params,
        id: `e-${params.source}-${params.target}-${Date.now()}`,
        type: 'elk',
        markerEnd: MarkerType.ArrowClosed,
        // 保留 handle 信息以支持当前会话的自定义连线
        sourceHandle: params.sourceHandle,
        targetHandle: params.targetHandle,
    };
    
    // UI 乐观更新
    edges.value.push(edgeParams);
    
    // 同步到后端 (后端只存拓扑，不存 Handle)
    noteStore.createEdge(params.source, params.target);
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
    edges.value = applyEdgeChanges(changes, edges.value);
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

const createNewNote = async () => {
  if (!checkAuth()) return;
  const defaultTitle = generateDefaultTitle();
  // Calculate center position or random
  const newNote = await noteStore.createNote(defaultTitle, '');
  if (newNote) {
    originalData.value.set(newNote.id, { 
        title: defaultTitle, 
        content: '', 
        weight: newNote.weight,
        start_at: newNote.start_at,
        node_type: null
    });
    
    // Add to graph
    const newNode = {
      id: newNote.id,
      label: newNote.title,
      position: { x: Math.random() * 500, y: Math.random() * 300 },
      data: { 
          title: newNote.title,
          weight: newNote.weight,
          node_type: null,
          created_at: newNote.created_at,
          start_at: newNote.start_at
      },
      type: 'custom'
    };
    nodes.value.push(newNode);
    
    selectNote(newNote.id);
  }
};

const selectNote = async (noteId: string) => {
  // 1. Check unsaved changes on current note
  if (currentNoteId.value && (saveStatus.value === 'unsaved' || saveStatus.value === 'saving')) {
      if (saveTimeout) {
          clearTimeout(saveTimeout);
          saveTimeout = null;
      }
      if (currentNote.value) {
          await saveNote(currentNote.value);
      }
  }

  // 2. Fetch detail if content is missing
  const note = noteStore.notes.find(n => n.id === noteId);
  if (note && note.content === undefined) {
      isFetchingContent.value = true;
      const detailed = await noteStore.fetchNoteDetail(noteId);
      if (detailed) {
          originalData.value.set(noteId, { 
              title: detailed.title, 
              content: detailed.content, 
              weight: detailed.weight,
              start_at: detailed.start_at,
              node_type: detailed.node_type || null
          });
      }
      isFetchingContent.value = false;
  }

  // 3. Switch
  currentNoteId.value = noteId;
  
  // 4. Reset status
  saveStatus.value = 'saved';
  if (saveTimeout) {
      clearTimeout(saveTimeout);
      saveTimeout = null;
  }
};

const onContentChange = (html: string) => {
  if (currentNote.value) {
    // 实时更新 store 中的 content，保证 v-model 同步
    currentNote.value.content = html;
    checkAndSave();
  }
};

const onTitleChange = () => {
    if (currentNote.value) {
        // 实时更新图上的节点标题
        const node = nodes.value.find(n => n.id === currentNote.value.id);
        if (node) {
            if (!node.data) node.data = {};
            node.data.title = currentNote.value.title;
        }
        checkAndSave();
    }
};

const onStartTimeChange = (value: number | string | Date | undefined) => {
    if (!currentNote.value) return;
    
    // Ensure value is valid timestamp number
    let newTime: number;
    if (typeof value === 'number') {
        newTime = value;
    } else if (typeof value === 'string') {
        newTime = parseInt(value, 10);
    } else if (value instanceof Date) {
        newTime = value.getTime();
    } else {
        return;
    }
    
    if (isNaN(newTime)) return;
    
    currentNote.value.start_at = newTime;
    
    // Update Graph Immediately
    const node = nodes.value.find(n => n.id === currentNote.value!.id);
    if (node && node.data) {
        node.data.start_at = newTime;
    }
    
    checkAndSave();
};

const onWeightChange = (value: number | undefined) => {
    if (!currentNote.value) return;
    
    // Validate Input
    if (value === undefined || value === null || isNaN(value)) {
        // Restore original value if invalid
        const original = originalData.value.get(currentNote.value.id);
        if (original) {
            currentNote.value.weight = original.weight; // You need to store weight in originalData first
        } else {
             currentNote.value.weight = 100; // Fallback default
        }
        ElMessage.warning('权重必须是有效数字');
        return;
    }
    
    // Round to integer
    const rounded = Math.round(value);
    if (rounded !== value) {
        currentNote.value.weight = rounded;
    }
    
    // Check range (ElInputNumber handles min/max, but double check)
    if (rounded < 1) {
        currentNote.value.weight = 1;
    }
    
    // Update Graph Immediately for feedback
    const node = nodes.value.find(n => n.id === currentNote.value.id);
    if (node && node.data) {
        node.data.weight = currentNote.value.weight;
    }
    
    // Trigger Save
    checkAndSave();
};

const onWeightBlur = (event: any) => {
    // Ensure validation runs on blur too if needed, though change covers most
    if (currentNote.value) {
        onWeightChange(currentNote.value.weight);
    }
};

const onNodeTypeChange = (value: string | null) => {
    if (!currentNote.value) return;
    
    // Update graph immediately
    const node = nodes.value.find(n => n.id === currentNote.value!.id);
    if (node && node.data) {
        node.data.node_type = value;
    }
    
    checkAndSave();
};

const getNodeTypeLabel = (type: string | null | undefined) => {
    if (type === 'project') return '项目 (Project)';
    if (type === 'module') return '模块 (Module)';
    if (type === 'todo') return '待办 (Todo)';
    if (type === 'doing') return '进行中 (Doing)';
    if (type === 'pre-done') return '预完成 (Pre-done)';
    if (type === 'done') return '完成 (Done)';
    if (type === 'delete') return '删除/放弃 (Delete)';
    if (type === 'bug') return '缺陷 (Bug)';
    if (type === 'memo') return '备忘 (Memo)';
    return '普通 (None)';
};

const getNodeTypeButtonType = (type: string | null | undefined) => {
    if (type === 'project') return 'primary';
    if (type === 'module') return 'primary';
    if (type === 'todo') return 'primary';
    if (type === 'doing') return 'warning';
    if (type === 'pre-done') return 'success';
    if (type === 'done') return 'info';
    if (type === 'delete') return 'info';
    if (type === 'bug') return 'danger';
    if (type === 'memo') return 'success'; // Greenish or neutral
    return '';
};

const checkAndSave = () => {
    if (!currentNote.value) return;
    
    const original = originalData.value.get(currentNote.value.id);
    if (!original) return;
    
    const isContentChanged = currentNote.value.content !== original.content;
    const isTitleChanged = currentNote.value.title !== original.title;
    const isWeightChanged = currentNote.value.weight !== original.weight;
    const isStartAtChanged = currentNote.value.start_at !== original.start_at;
    const isNodeTypeChanged = currentNote.value.node_type !== original.node_type;
    
    if (!isContentChanged && !isTitleChanged && !isWeightChanged && !isStartAtChanged && !isNodeTypeChanged) {
        if (saveStatus.value === 'unsaved') {
            saveStatus.value = 'saved';
        }
        return;
    }
    
    saveStatus.value = 'unsaved';
    
    if (saveTimeout) clearTimeout(saveTimeout);
    saveTimeout = setTimeout(() => {
        saveNote(currentNote.value);
    }, 2000);
};

const saveNote = async (noteToSave: NoteNode | undefined = undefined) => {
  const target = noteToSave || currentNote.value;
  if (!target) return;
  
  saveStatus.value = 'saving';
  
  // Find node in graph to update position if needed (later)
  // For now just update content/title
  
  await noteStore.updateNote(target.id, {
    title: target.title,
    content: target.content,
    weight: target.weight,
    start_at: target.start_at,
    node_type: target.node_type
  });
  
  // Update graph label and weight
  const node = nodes.value.find(n => n.id === target.id);
  if (node) {
      node.label = target.title;
      // 必须同时更新 data.title，因为 CustomNode 是从 data.title 读取显示的
      if (node.data) {
          node.data.title = target.title;
          node.data.weight = target.weight;
          node.data.start_at = target.start_at;
          node.data.node_type = target.node_type;
      } else {
          node.data = { 
              title: target.title, 
              weight: target.weight, 
              start_at: target.start_at,
              node_type: target.node_type 
          };
      }
  }
  
  originalData.value.set(target.id, { 
      title: target.title, 
      content: target.content, 
      weight: target.weight,
      start_at: target.start_at,
      node_type: target.node_type 
  });
  
  if (currentNoteId.value === target.id) {
      saveStatus.value = 'saved';
  }
};

const deleteCurrentNote = async () => {
    if (!currentNote.value) return;
    if (!checkAuth()) return;
    
    try {
        await ElMessageBox.confirm('确定要删除这个节点吗？', '警告', {
            confirmButtonText: '删除',
            cancelButtonText: '取消',
            type: 'warning'
        });
        
        await noteStore.deleteNote(currentNote.value.id);
        
        // Remove from graph
        nodes.value = nodes.value.filter(n => n.id !== currentNoteId.value);
        currentNoteId.value = '';
        
    } catch (e) {
        // Cancelled
    }
};

const formatDateDetailed = (timestamp: number) => {
    const date = new Date(timestamp);
    const now = new Date();
    const isCurrentYear = date.getFullYear() === now.getFullYear();
    
    const options: Intl.DateTimeFormatOptions = {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
    };
    
    if (!isCurrentYear) {
        options.year = 'numeric';
    }
    
    return date.toLocaleString('zh-CN', options).replace(/-/g, '/');
};
</script>

<style scoped>
.task-manager-layout {
  display: flex;
  flex-direction: column;
  min-height: 100vh; /* Changed from height: 100% to allow growth */
  overflow-x: hidden;
  overflow-y: auto; /* Allow the whole page to scroll if editor is long */
}

.filter-section {
    display: flex;
    align-items: center;
    gap: 20px;
    padding: 10px 20px;
    background: #f5f7fa;
    border-bottom: 1px solid #e6e6e6;
    height: 60px; /* Fixed height for filter bar */
    box-sizing: border-box;
}

.slider-section {
    display: flex;
    flex-direction: column;
    padding: 10px 20px;
    background: #fff;
    border-bottom: 1px solid #e6e6e6;
    box-sizing: border-box;
    gap: 10px;
}

.slider-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 5px;
}

.slider-header .title {
    font-size: 14px;
    font-weight: bold;
    color: #303133;
}

.slider-header .el-icon {
    font-size: 14px;
    color: #909399;
    cursor: help;
}

.slider-item {
    display: flex;
    align-items: center;
    gap: 20px;
}

.slider-item .label {
    font-size: 12px;
    color: #606266;
    white-space: nowrap;
    min-width: 100px;
}

/* 确保滑块和密度条完美重叠 */
.slider-container {
    position: relative;
    height: 54px; /* 增加高度以容纳刻度轴 */
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
}

.density-bar {
    position: absolute;
    top: 14px; /* 与滑块轨道对齐 */
    left: 0;
    right: 0;
    height: 12px; /* 比滑块轨道(6px)高，使其溢出可见 */
    display: flex;
    pointer-events: none; /* 让鼠标事件穿透给 Slider */
    z-index: 0; /* 在滑块下方 */
    border-radius: 3px;
    overflow: hidden;
    background-color: #f2f3f5; /* 默认更浅的灰色背景 */
    opacity: 0.8;
}

.density-bin {
    flex: 1;
    height: 100%;
}

.slider-item .el-slider {
    position: absolute; /* 覆盖在密度条上 */
    top: 4px;
    left: 0;
    width: 100%;
    z-index: 1;
}

/* 隐藏 el-slider 的默认轨道背景 */
.slider-item :deep(.el-slider__runway) {
    background-color: transparent !important;
    height: 6px; /* 显式设置轨道高度 */
    margin: 16px 0; /* 给手柄留出空间 */
}

/* 让选中的 Bar 变为半透明，从而透出下方的密度条 */
.slider-item :deep(.el-slider__bar) {
    background-color: rgba(64, 158, 255, 0.4) !important;
    height: 6px;
}

/* 将手柄改为垂直细线样式 */
.slider-item :deep(.el-slider__button) {
    width: 2px;
    height: 20px;
    border-radius: 0;
    background-color: #409eff;
    border: none;
}

/* 增加手柄的可点击区域，但不影响视觉 */
.slider-item :deep(.el-slider__button-wrapper) {
    width: 32px;
    height: 32px;
    display: flex;
    justify-content: center;
    align-items: center;
}

/* 刻度轴样式 */
.time-axis {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: 16px;
    pointer-events: none;
}

.tick {
    position: absolute;
    bottom: 0;
    transform: translateX(-50%);
    display: flex;
    flex-direction: column;
    align-items: center;
}

.tick::before {
    content: '';
    width: 1px;
    height: 4px;
    background-color: #dcdfe6;
    margin-bottom: 2px;
}

.tick-label {
    font-size: 10px;
    color: #909399;
    white-space: nowrap;
}

.scope-info {
    display: flex;
    align-items: center;
    gap: 12px;
    flex: 1;
}

.scope-tags {
    display: flex;
    gap: 8px;
}

.text-gray {
    color: #909399;
    font-size: 13px;
}

.actions {
    display: flex;
    gap: 10px;
}

.filter-dialog-content {
    padding: 10px 0;
}

.form-tip {
    font-size: 12px;
    color: #909399;
    margin-top: 5px;
}

.now-marker {
    position: absolute;
    top: 50%;
    width: 2px;
    height: 18px;
    background-color: #f56c6c;
    z-index: 2;
    transform: translateY(-50%);
    pointer-events: none;
}

.now-marker::after {
    content: '当前';
    position: absolute;
    top: -15px;
    left: 50%;
    transform: translateX(-50%);
    font-size: 10px;
    color: #f56c6c;
    white-space: nowrap;
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

/* Remove old filter panel styles */

.editor-section {
  flex: 1; /* Take remaining space but can grow */
  display: flex;
  flex-direction: column;
  background-color: #fff;
  min-height: 600px; /* Increased from 400px */
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
