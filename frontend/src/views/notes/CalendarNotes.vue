<template>
  <div class="calendar-notes-layout">
    <!-- Header: Controls -->
    <div class="header-section">
      <div class="left-controls">
        <el-date-picker
          v-model="currentMonth"
          type="month"
          placeholder="选择月份"
          :clearable="false"
          @change="onMonthChange"
        />
        <el-button @click="prevMonth" :icon="ArrowLeft" circle />
        <el-button @click="nextMonth" :icon="ArrowRight" circle />
        <el-button @click="goToToday">今天</el-button>
        <span class="current-month-label">{{ formatMonthLabel(currentMonth) }}</span>
      </div>
      <div class="right-controls">
        <el-button @click="refreshData" :icon="Refresh">刷新</el-button>
      </div>
    </div>

    <!-- Calendar Grid -->
    <div class="calendar-container" :style="{ height: calendarHeight + 'px' }">
      <!-- Weekday Headers -->
      <div class="weekday-header">
        <div v-for="day in weekDays" :key="day" class="weekday-cell">{{ day }}</div>
      </div>

      <!-- Days Grid -->
      <div class="days-grid" :style="{ gridTemplateRows }">
        <div v-for="day in gridDays" :key="day.dateStr" class="day-cell" :class="{ 'is-outside': !day.isCurrentMonth }">
          <div class="day-number" :class="{ 'is-today': isToday(day.date) }">
            <div class="day-left">
              <span class="solar-day" :class="{ 'is-rest-text': day.isRest }">{{ day.dayNum }}</span>
              <span v-if="isToday(day.date)" class="today-tag">今天</span>
              <span v-if="day.holidayName" class="holiday-marker" :class="{ 'is-rest': day.isRest === true, 'is-work': day.isRest === false }">
                {{ day.isRest === true ? '休' : '班' }}
              </span>
              <el-button class="create-note-btn" size="small" text circle :icon="Plus" title="新建节点" @click.stop="createNoteForDay(day.date)" />
            </div>
            <div class="day-right">
              <span class="lunar-info" :class="{ 'is-festival': day.festival || day.jieQi }">
                {{ day.festival || day.jieQi || day.lunarDay }}
              </span>
            </div>
          </div>
          <div class="day-content">
            <div 
              v-for="note in getNotesForDay(day.date)" 
              :key="note.id" 
              class="note-item"
              :style="getNoteStyle(note)"
              @click.stop="openNote(note)"
            >
              <span class="note-title" :style="getNoteTitleStyle(note)">{{ note.title }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Height Resizer Handle -->
      <div class="calendar-resizer" @mousedown="startResizing">
          <div class="resizer-indicator"></div>
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
      />
      <div v-else class="empty-state">
        <el-empty description="请在日历中选择一个节点" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useRouter } from 'vue-router';
import { useNoteStore, type NoteNode } from '@/api/notes';
import { useUserStore } from '@/store/userStore';
import { Refresh, ArrowLeft, ArrowRight, Plus } from '@element-plus/icons-vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { Solar, HolidayUtil } from 'lunar-javascript';
import { getNodeStyle } from '@/utils/nodeConfig';
import NoteDetailPanel from '@/components/NoteDetailPanel.vue';

const router = useRouter();
const noteStore = useNoteStore();
const userStore = useUserStore();

const currentMonth = ref<Date>(new Date());
const weekDays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const currentNoteId = ref('');

const pad2 = (n: number) => String(n).padStart(2, '0');

const toDateStr = (d: Date) => {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
};

const monthAnchor = computed(() => {
  const d = currentMonth.value instanceof Date ? currentMonth.value : new Date();
  return new Date(d.getFullYear(), d.getMonth(), 1);
});

const monthStartTs = computed(() => monthAnchor.value.getTime());
const monthEndTs = computed(() => new Date(monthAnchor.value.getFullYear(), monthAnchor.value.getMonth() + 1, 1).getTime());

const formatMonthLabel = (d: Date) => {
  const year = d.getFullYear();
  const month = pad2(d.getMonth() + 1);
  return `${year}年${month}月`;
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

const createNoteForDay = async (date: Date) => {
  if (!checkAuth()) return;
  const now = new Date();
  const isTargetToday = isToday(date);
  
  const yy = String(date.getFullYear()).slice(-2);
  const mm = pad2(date.getMonth() + 1);
  const dd = pad2(date.getDate());
  
  let hh = '00';
  let min = '00';
  let startAt: number;

  if (isTargetToday) {
    hh = pad2(now.getHours());
    min = pad2(now.getMinutes());
    // 如果是今天，保留当前时分秒
    startAt = new Date(date.getFullYear(), date.getMonth(), date.getDate(), now.getHours(), now.getMinutes(), now.getSeconds()).getTime();
  } else {
    // 非今天，时分秒归零
    startAt = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 0, 0, 0, 0).getTime();
  }
  
  const defaultTitle = `${yy}${mm}${dd}_${hh}${min}`;

  const newNote = await noteStore.createNote(defaultTitle, '', 100, startAt);
  if (newNote) {
    currentNoteId.value = newNote.id;
    ElMessage.success('已创建节点');
  }
};

const onMonthChange = (value: Date | string | number | undefined) => {
  const d = value instanceof Date ? value : value ? new Date(value) : new Date();
  if (Number.isNaN(d.getTime())) return;
  currentMonth.value = new Date(d.getFullYear(), d.getMonth(), 1);
  refreshData();
};

const prevMonth = () => {
  const d = monthAnchor.value;
  currentMonth.value = new Date(d.getFullYear(), d.getMonth() - 1, 1);
  refreshData();
};

const nextMonth = () => {
  const d = monthAnchor.value;
  currentMonth.value = new Date(d.getFullYear(), d.getMonth() + 1, 1);
  refreshData();
};

const goToToday = () => {
  const d = new Date();
  currentMonth.value = new Date(d.getFullYear(), d.getMonth(), 1);
  refreshData();
};

const isToday = (d: Date) => {
  const now = new Date();
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
};

type CalendarDay = {
  date: Date;
  dateStr: string;
  dayNum: number;
  isCurrentMonth: boolean;
  lunarDay: string;
  festival?: string;
  jieQi?: string;
  holidayName?: string;
  isRest?: boolean | null;
};

const buildDayMeta = (date: Date) => {
  let lunarDay = '';
  let festival: string | undefined;
  let jieQi: string | undefined;
  let holidayName: string | undefined;
  let isRest: boolean | null | undefined;

  try {
    const solar = Solar.fromDate(date as any);
    const lunar = solar.getLunar?.();
    const dayInChinese = lunar?.getDayInChinese?.();
    const monthInChinese = lunar?.getMonthInChinese?.();
    lunarDay = dayInChinese === '初一' && monthInChinese ? `${monthInChinese}月` : (dayInChinese || '');

    const lunarFestivals: string[] = lunar?.getFestivals?.() || [];
    const solarFestivals: string[] = solar.getFestivals?.() || [];
    festival = [...lunarFestivals, ...solarFestivals][0];
    jieQi = lunar?.getJieQi?.() || undefined;

    const h = HolidayUtil.getHoliday?.(date.getFullYear(), date.getMonth() + 1, date.getDate());
    if (h) {
      holidayName = h.getName?.() || h.getName || undefined;
      if (typeof h.isWork === 'function') isRest = !h.isWork();
      else if (typeof h.isRest === 'function') isRest = h.isRest();
      else if (typeof h.getWork === 'function') isRest = h.getWork() !== 1;
      else isRest = true;
    }
  } catch (e) {
    lunarDay = '';
  }

  return { lunarDay, festival, jieQi, holidayName, isRest: isRest ?? null };
};

const startPadding = computed(() => {
  const first = new Date(monthAnchor.value.getFullYear(), monthAnchor.value.getMonth(), 1);
  const day = first.getDay();
  return (day + 6) % 7;
});

const gridDays = computed<CalendarDay[]>(() => {
  const d = monthAnchor.value;
  const year = d.getFullYear();
  const month = d.getMonth();
  const dayCount = new Date(year, month + 1, 0).getDate();

  const totalCells = Math.ceil((startPadding.value + dayCount) / 7) * 7;
  const gridStart = new Date(year, month, 1 - startPadding.value);

  const list: CalendarDay[] = [];
  for (let i = 0; i < totalCells; i += 1) {
    const date = new Date(gridStart);
    date.setDate(gridStart.getDate() + i);
    const meta = buildDayMeta(date);
    list.push({
      date,
      dateStr: toDateStr(date),
      dayNum: date.getDate(),
      isCurrentMonth: date.getFullYear() === year && date.getMonth() === month,
      lunarDay: meta.lunarDay,
      festival: meta.festival,
      jieQi: meta.jieQi,
      holidayName: meta.holidayName,
      isRest: meta.isRest
    });
  }
  return list;
});

const gridTemplateRows = computed(() => {
  const days = gridDays.value;
  if (!days.length) return '';
  
  const rowsCount = days.length / 7;
  const weights: number[] = [];
  
  for (let i = 0; i < rowsCount; i++) {
    let maxNodesInWeek = 0;
    // 检查这一周的每一天
    for (let j = 0; j < 7; j++) {
      const dayIndex = i * 7 + j;
      const dayNotes = getNotesForDay(days[dayIndex].date);
      if (dayNotes.length > maxNodesInWeek) {
        maxNodesInWeek = dayNotes.length;
      }
    }
    // 权重 = 5 + 该周单日最大节点数
    weights.push(5 + maxNodesInWeek);
  }
  
  return weights.map(w => `${w}fr`).join(' ');
});

const gridStartTs = computed(() => gridDays.value[0]?.date.getTime() ?? monthStartTs.value);
const gridEndTs = computed(() => {
  const last = gridDays.value[gridDays.value.length - 1]?.date;
  if (!last) return monthEndTs.value;
  return new Date(last.getFullYear(), last.getMonth(), last.getDate() + 1).getTime();
});

const notesByDay = computed(() => {
  const map = new Map<string, NoteNode[]>();
  const start = gridStartTs.value;
  const end = gridEndTs.value;

  for (const note of noteStore.notes) {
    const ts = note.start_at || note.created_at;
    if (!ts || ts < start || ts >= end) continue;
    const key = toDateStr(new Date(ts));
    const arr = map.get(key) || [];
    arr.push(note);
    map.set(key, arr);
  }

  for (const arr of map.values()) {
    // 优先按时间（start_at 或 created_at）升序排序，时间相同时按权重降序
    arr.sort((a, b) => {
      const timeA = a.start_at || a.created_at;
      const timeB = b.start_at || b.created_at;
      if (timeA !== timeB) return timeA - timeB;
      return (b.weight || 0) - (a.weight || 0);
    });
  }

  return map;
});

const getNotesForDay = (date: Date) => {
  return notesByDay.value.get(toDateStr(date)) || [];
};

const openNote = (note: NoteNode) => {
  currentNoteId.value = note.id;
};

const getNoteStyle = (note: NoteNode) => {
  const style = getNodeStyle(note.node_type, note.node_status);
  
  // Calculate height based on weight
  // Default weight 100 -> height 24px (standard line height + padding)
  // Scale similar to star map: sqrt(weight/100)
  const safeWeight = Math.max(10, note.weight || 100);
  const scale = Math.sqrt(safeWeight / 100);
  // Base height ~26px (standard)
  // For weight 100 (scale 1) -> 26px
  // For weight 400 (scale 2) -> 52px
  const baseHeight = 26;
  const height = Math.round(baseHeight * scale);
  
  return {
    marginBottom: '4px',
    padding: '0 6px',
    borderRadius: '4px',
    borderColor: style.borderColor,
    borderWidth: style.borderWidth,
    borderStyle: style.borderStyle,
    backgroundColor: style.backgroundColor,
    opacity: style.opacity,
    cursor: 'pointer',
    overflow: 'hidden',
    height: `${height}px`,
    display: 'flex',
    alignItems: 'center'
  } as any;
};

const getNoteTitleStyle = (note: NoteNode) => {
  const style = getNodeStyle(note.node_type, note.node_status);
  const safeWeight = Math.max(10, note.weight || 100);
  const scale = Math.sqrt(safeWeight / 100);
  // Font size: Base 12px, grow slower to allow more text
  // Default weight 100 (scale 1) -> 12px
  // Weight 400 (scale 2) -> 14px
  // Weight 900 (scale 3) -> 16px
  const fontSize = Math.min(16, Math.max(12, Math.round(12 + (scale - 1) * 2)));
  
  // Recalculate height to determine max lines
  const baseHeight = 26;
  const height = Math.round(baseHeight * scale);
  
  const lineHeight = 1.25;
  const lineHeightPx = fontSize * lineHeight;
  // Calculate max lines based on height
  const maxLines = Math.max(1, Math.floor(height / lineHeightPx));

  return {
    color: style.color,
    fontWeight: style.fontWeight,
    textDecoration: style.textDecoration,
    fontSize: `${fontSize}px`,
    lineHeight: lineHeight,
    width: '100%',
    // Multi-line ellipsis
    display: '-webkit-box',
    WebkitBoxOrient: 'vertical',
    WebkitLineClamp: maxLines,
    overflow: 'hidden',
    wordBreak: 'break-all'
  } as any;
};

const refreshData = async () => {
  await noteStore.fetchNotes({ limit: 5000 });
  ElMessage.success('已刷新');
};

const handleNoteUpdate = () => {};

const handleNoteDelete = (noteId: string) => {
  if (currentNoteId.value === noteId) currentNoteId.value = '';
};

// Layout state
const calendarHeight = ref(600);
const isResizing = ref(false);
const isManualResized = ref(false);
const startY = ref(0);
const startHeight = ref(0);

const calculateOptimalHeight = () => {
    const vh = window.innerHeight;
    const reservedHeight = 100; // Header ~60px + margins
    // Default to 60% height for calendar
    return Math.max(400, Math.floor((vh - reservedHeight) * 0.6));
};

const updateAdaptiveHeight = () => {
    if (!isManualResized.value) {
        calendarHeight.value = calculateOptimalHeight();
    }
};

const startResizing = (e: MouseEvent) => {
    isResizing.value = true;
    isManualResized.value = true;
    startY.value = e.clientY;
    startHeight.value = calendarHeight.value;
    
    window.addEventListener('mousemove', handleResizing);
    window.addEventListener('mouseup', stopResizing);
    document.body.style.userSelect = 'none';
};

const handleResizing = (e: MouseEvent) => {
    if (!isResizing.value) return;
    const delta = e.clientY - startY.value;
    const newHeight = Math.max(300, startHeight.value + delta);
    calendarHeight.value = newHeight;
};

const stopResizing = () => {
    isResizing.value = false;
    window.removeEventListener('mousemove', handleResizing);
    window.removeEventListener('mouseup', stopResizing);
    document.body.style.userSelect = '';
};

onMounted(() => {
    updateAdaptiveHeight();
    window.addEventListener('resize', updateAdaptiveHeight);
    refreshData();
});

onUnmounted(() => {
    window.removeEventListener('resize', updateAdaptiveHeight);
});

</script>

<style scoped>
.calendar-notes-layout {
  display: flex;
  flex-direction: column;
  height: 100%;
  background-color: #fff;
  padding: 20px;
  box-sizing: border-box;
  overflow: hidden;
}

.header-section {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.left-controls {
  display: flex;
  align-items: center;
  gap: 10px;
}

.current-month-label {
  font-size: 18px;
  font-weight: bold;
  margin-left: 10px;
}

.calendar-container {
  display: flex;
  flex-direction: column;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  overflow: hidden;
  flex: none; /* Fixed height managed by JS resizer */
  position: relative;
  margin-bottom: 20px;
}

.weekday-header {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  background-color: #f5f7fa;
  border-bottom: 1px solid #ebeef5;
}

.weekday-cell {
  padding: 10px;
  text-align: center;
  font-weight: bold;
  color: #606266;
  border-right: 1px solid #ebeef5;
}

.weekday-cell:last-child {
  border-right: none;
}

.days-grid {
  flex: 1; /* This is crucial: the grid should fill the container's height */
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  grid-auto-rows: 1fr; /* All rows get equal height based on the container */
  overflow: hidden; /* Grid itself doesn't scroll, cells do */
}

.day-cell {
  border-right: 1px solid #ebeef5;
  border-bottom: 1px solid #ebeef5;
  padding: 5px;
  display: flex;
  flex-direction: column;
  overflow: hidden; /* Each cell contains its scrollable content */
}

.day-cell:nth-child(7n) {
  border-right: none;
}

.padding-cell {
  background-color: #fcfcfc;
}

.day-cell.is-outside {
  background-color: #fcfcfc;
}

.day-cell.is-outside .solar-day {
  color: #909399;
}

.day-cell.is-outside .lunar-info {
  color: #c0c4cc;
}

.create-note-btn {
  margin-left: 4px;
  opacity: 0;
}

.day-cell:hover .create-note-btn {
  opacity: 1;
}

.day-number {
  font-size: 14px;
  font-weight: bold;
  color: #303133;
  margin-bottom: 5px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.day-left {
  display: flex;
  align-items: center;
  gap: 4px;
}

.solar-day.is-rest-text {
  color: #f56c6c;
}

.day-right {
  display: flex;
  align-items: center;
}

.lunar-info {
  font-size: 11px;
  font-weight: normal;
  color: #909399;
}

.lunar-info.is-festival {
  color: #f56c6c;
}

.holiday-marker {
  font-size: 10px;
  padding: 1px 2px;
  border-radius: 2px;
  line-height: 1;
}

.holiday-marker.is-rest {
  background-color: #fef0f0;
  color: #f56c6c;
}

.holiday-marker.is-work {
  background-color: #ecf5ff;
  color: #409eff;
}

.is-today {
  color: #409eff;
}

.today-tag {
  font-size: 10px;
  background-color: #ecf5ff;
  color: #409eff;
  padding: 1px 4px;
  border-radius: 2px;
}

.day-content {
  flex: 1;
  overflow-y: auto;
}

/* 隐藏日历格内部的滚动条以保持简洁，仅在需要时显示 */
.day-content::-webkit-scrollbar {
  width: 4px;
}
.day-content::-webkit-scrollbar-thumb {
  background: #eee;
  border-radius: 2px;
}
.day-content:hover::-webkit-scrollbar-thumb {
  background: #ccc;
}

.note-item {
  transition: all 0.2s;
}

.note-item:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}
  /* New styles for resizer and layout */
  .calendar-resizer {
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

  .calendar-resizer:hover {
    background-color: #ecf5ff;
  }

  .resizer-indicator {
    width: 40px;
    height: 4px;
    border-top: 1px solid #dcdfe6;
    border-bottom: 1px solid #dcdfe6;
  }

  .editor-section {
    flex: none; /* Don't flex, just follow the flow */
    display: flex;
    flex-direction: column;
    background-color: #fff;
    min-height: 400px;
    border-top: 1px solid #ebeef5;
    overflow: visible; /* Let parent scroll */
  }

  .editor-wrapper {
    display: flex;
    flex-direction: column;
    height: auto;
    padding: 20px;
  }

  .empty-state {
    display: flex;
    justify-content: center;
    align-items: center;
    height: 100%;
    color: #909399;
  }
</style>
