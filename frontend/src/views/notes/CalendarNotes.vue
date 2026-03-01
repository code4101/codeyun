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
    <div class="calendar-container" :style="{ height: calendarHeight + 'px' }">
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
              @click.stop="openNote(note)"
            >
              <span class="note-title" :style="getNoteTitleStyle(note)">{{ note.title }}</span>
            </div>
          </div>
        </div>
        
        <!-- Empty cells for padding at end (optional, to fill row) -->
        <div v-for="n in endPadding" :key="'end-pad-' + n" class="day-cell padding-cell"></div>
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
import { Back, Refresh, ArrowLeft, ArrowRight } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import { Solar, HolidayUtil } from 'lunar-javascript';
import { getNodeConfig } from '@/utils/nodeConfig';
import NoteDetailPanel from '@/components/NoteDetailPanel.vue';

const router = useRouter();
const noteStore = useNoteStore();

const currentMonth = ref(new Date());
const weekDays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const currentNoteId = ref('');

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
  /* New styles for resizer and layout */
  .calendar-container {
    height: 600px; /* Default, overridden by style */
    flex: none; /* Don't flex, use fixed height */
    position: relative;
  }

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
    flex: 1;
    display: flex;
    flex-direction: column;
    background-color: #fff;
    min-height: 400px;
    border-top: 1px solid #ebeef5;
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
