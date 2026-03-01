<template>
  <div class="calendar-notes-layout">
    <!-- Header: Controls -->
    <div class="header-section">
      <div class="left-controls">
        <el-button @click="router.push('/notes/star-map')" :icon="Back">返回星图</el-button>
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
    <div class="calendar-container">
      <!-- Weekday Headers -->
      <div class="weekday-header">
        <div v-for="day in weekDays" :key="day" class="weekday-cell">{{ day }}</div>
      </div>

      <!-- Days Grid -->
      <div class="days-grid">
        <!-- Empty cells for padding at start -->
        <div v-for="n in startPadding" :key="'pad-' + n" class="day-cell padding-cell"></div>

        <!-- Actual Days -->
        <div v-for="day in daysInMonth" :key="day.dateStr" class="day-cell">
          <div class="day-number" :class="{ 'is-today': isToday(day.date) }">
            <div class="day-left">
              <span class="solar-day" :class="{ 'is-rest-text': day.isRest }">{{ day.dayNum }}</span>
              <span v-if="isToday(day.date)" class="today-tag">今天</span>
              <span v-if="day.holidayName" class="holiday-marker" :class="{ 'is-rest': day.isRest === true, 'is-work': day.isRest === false }">
                {{ day.isRest === true ? '休' : '班' }}
              </span>
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
              @click="openNote(note)"
            >
              <span class="note-title" :style="getNoteTitleStyle(note)">{{ note.title }}</span>
            </div>
          </div>
        </div>
        
        <!-- Empty cells for padding at end (optional, to fill row) -->
        <div v-for="n in endPadding" :key="'end-pad-' + n" class="day-cell padding-cell"></div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { useNoteStore, type NoteNode } from '@/api/notes';
import { Back, Refresh, ArrowLeft, ArrowRight } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { Solar, HolidayUtil } from 'lunar-javascript';

const router = useRouter();
const noteStore = useNoteStore();

const currentMonth = ref(new Date());
const weekDays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

const daysInMonth = computed(() => {
  const year = currentMonth.value.getFullYear();
  const month = currentMonth.value.getMonth();
  const date = new Date(year, month, 1);
  const days = [];
  
  while (date.getMonth() === month) {
    const solar = Solar.fromDate(date);
    const lunar = solar.getLunar();
    const festivals = [...lunar.getFestivals(), ...solar.getFestivals()];
    const jieQi = lunar.getJieQi();
    const holiday = HolidayUtil.getHoliday(year, month + 1, date.getDate());
    
    // Determine if it's a rest day: 
    // 1. If holiday exists, use holiday's work/rest status
    // 2. If no holiday, use weekend status (Sat/Sun)
    let isRest = false;
    if (holiday) {
      isRest = !holiday.isWork();
    } else {
      const dayOfWeek = date.getDay();
      isRest = dayOfWeek === 0 || dayOfWeek === 6;
    }

    days.push({
      date: new Date(date),
      dayNum: date.getDate(),
      dateStr: date.toISOString().split('T')[0],
      lunarDay: lunar.getDayInChinese(),
      lunarMonth: lunar.getMonthInChinese(),
      festival: festivals.length > 0 ? festivals[0] : null,
      jieQi: jieQi || null,
      isRest: isRest,
      holidayName: holiday ? holiday.getName() : null
    });
    date.setDate(date.getDate() + 1);
  }
  return days;
});

const startPadding = computed(() => {
  const year = currentMonth.value.getFullYear();
  const month = currentMonth.value.getMonth();
  const firstDay = new Date(year, month, 1).getDay();
  // Monday is 1, Sunday is 0. We want Monday as start (0 padding if Mon, 6 padding if Sun)
  // map: 1->0, 2->1, ... 6->5, 0->6
  return firstDay === 0 ? 6 : firstDay - 1;
});

const endPadding = computed(() => {
  const totalCells = startPadding.value + daysInMonth.value.length;
  const remainder = totalCells % 7;
  return remainder === 0 ? 0 : 7 - remainder;
});

const formatMonthLabel = (date: Date) => {
  return `${date.getFullYear()}年 ${date.getMonth() + 1}月`;
};

const onMonthChange = () => {
  // Logic to maybe fetch data for specific range if backend supports it
  // For now we rely on store having data or fetch all
  refreshData();
};

const prevMonth = () => {
  const d = new Date(currentMonth.value);
  d.setMonth(d.getMonth() - 1);
  currentMonth.value = d;
  onMonthChange();
};

const nextMonth = () => {
  const d = new Date(currentMonth.value);
  d.setMonth(d.getMonth() + 1);
  currentMonth.value = d;
  onMonthChange();
};

const goToToday = () => {
  currentMonth.value = new Date();
  onMonthChange();
};

const isToday = (date: Date) => {
  const today = new Date();
  return date.getDate() === today.getDate() &&
         date.getMonth() === today.getMonth() &&
         date.getFullYear() === today.getFullYear();
};

const getNotesForDay = (date: Date) => {
  const startOfDay = new Date(date);
  startOfDay.setHours(0, 0, 0, 0);
  const endOfDay = new Date(date);
  endOfDay.setHours(23, 59, 59, 999);
  
  const startTs = startOfDay.getTime();
  const endTs = endOfDay.getTime();
  
  return noteStore.notes.filter(n => n.start_at >= startTs && n.start_at <= endTs)
    .sort((a, b) => a.start_at - b.start_at);
};

const refreshData = async () => {
  // Ideally we should fetch by range, but existing API might be all or nothing
  // For now, reuse the fetch logic.
  // We can construct a range for the current month view
  const year = currentMonth.value.getFullYear();
  const month = currentMonth.value.getMonth();
  
  // Get start of view (including padding)
  const startView = new Date(year, month, 1);
  startView.setDate(startView.getDate() - startPadding.value);
  startView.setHours(0,0,0,0);
  
  // Get end of view
  const endView = new Date(year, month + 1, 0); // last day of month
  endView.setDate(endView.getDate() + endPadding.value);
  endView.setHours(23,59,59,999);
  
  await noteStore.fetchNotes({
    created_start: startView.getTime(),
    created_end: endView.getTime()
  });
};

// Styling Logic extracted from CustomNode.vue
const getNoteStyle = (note: NoteNode) => {
    const style: any = {
        borderWidth: '1px',
        borderStyle: 'solid',
        borderRadius: '4px',
        marginBottom: '4px',
        padding: '2px 4px',
        cursor: 'pointer',
        fontSize: '12px',
        backgroundColor: '#fff',
        borderColor: '#dcdfe6'
    };

    const type = note.node_type;

    if (type === 'project') {
        style.borderColor = '#9c27b0';
        style.borderWidth = '2px';
        style.backgroundColor = '#f3e5f5';
    } else if (type === 'module') {
        style.borderColor = '#ba68c8';
        style.borderWidth = '2px';
        style.backgroundColor = '#faf4fb';
    } else if (type === 'todo') {
        style.borderColor = '#409eff';
        style.borderWidth = '1px';
    } else if (type === 'doing') {
        style.borderColor = '#e6a23c';
        style.borderWidth = '1px';
        style.backgroundColor = '#fdf6ec';
    } else if (type === 'pre-done') {
        style.borderColor = '#67c23a';
        style.borderWidth = '1px';
        style.borderStyle = 'dashed';
        style.backgroundColor = '#f0f9eb';
    } else if (type === 'done') {
        style.borderColor = '#e6e6e6';
        style.backgroundColor = '#fafafa';
    } else if (type === 'delete') {
        style.borderColor = '#dcdfe6';
        style.backgroundColor = '#f5f7fa';
        style.opacity = '0.6';
    } else if (type === 'bug') {
        style.borderColor = '#f56c6c';
        style.backgroundColor = '#fef0f0';
        style.borderWidth = '1px';
    } else if (type === 'memo') {
        style.borderColor = '#303133';
        style.borderWidth = '1px';
    }
    
    return style;
};

const getNoteTitleStyle = (note: NoteNode) => {
    const style: any = {
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
        display: 'block'
    };
    
    const type = note.node_type;

    if (type === 'project') {
        style.color = '#7b1fa2';
        style.fontWeight = 'bold';
    } else if (type === 'module') {
        style.color = '#9c27b0';
        style.fontWeight = 'bold';
    } else if (type === 'todo') {
        style.color = '#409eff';
    } else if (type === 'doing') {
        style.color = '#e6a23c';
        style.fontWeight = 'bold';
    } else if (type === 'pre-done') {
        style.color = '#67c23a';
    } else if (type === 'done') {
        style.color = '#909399';
    } else if (type === 'delete') {
        style.color = '#c0c4cc';
        style.textDecoration = 'line-through';
    } else if (type === 'bug') {
        style.color = '#f56c6c';
        style.fontWeight = 'bold';
    } else if (type === 'memo') {
        style.color = '#000000';
        style.fontWeight = 'bold';
    }
    
    return style;
};

const openNote = (note: NoteNode) => {
    // Navigate back to star map and focus/select this note?
    // Or just show details?
    // For now, let's navigate back to star map with query param to select it
    // But StarNotes might not support query param selection yet. 
    // Let's just stay here or maybe show a dialog. 
    // Requirement says "display node list", doesn't specify interaction.
    // I'll leave it as just visual for now, or maybe simple toast.
    ElMessage.info(`节点: ${note.title}`);
};

onMounted(() => {
  refreshData();
});
</script>

<style scoped>
.calendar-notes-layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background-color: #fff;
  padding: 20px;
  box-sizing: border-box;
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
  flex: 1;
  display: flex;
  flex-direction: column;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  overflow: hidden;
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
  flex: 1;
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  grid-auto-rows: minmax(100px, 1fr); /* Ensure cells have height */
  overflow-y: auto;
}

.day-cell {
  border-right: 1px solid #ebeef5;
  border-bottom: 1px solid #ebeef5;
  padding: 5px;
  display: flex;
  flex-direction: column;
  min-height: 120px;
}

.day-cell:nth-child(7n) {
  border-right: none;
}

.padding-cell {
  background-color: #fcfcfc;
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

.note-item {
  transition: all 0.2s;
}

.note-item:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}
</style>
