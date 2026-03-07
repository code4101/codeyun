<template>
  <div class="notes-center">
    <div class="header">
      <h2>星图笔记</h2>
      <div class="header-actions">
        <!-- Placeholder for global actions if any -->
      </div>
    </div>

    <el-tabs 
      v-model="activeTabId" 
      class="notes-tabs" 
      @tab-change="handleTabChange"
      @tab-remove="handleTabRemove"
      type="card"
    >
      <el-tab-pane 
        v-for="tab in tabs" 
        :key="tab.id" 
        :label="tab.label" 
        :name="tab.id"
        :closable="tab.closable"
        lazy
      >
        <component
          :is="getTabComponent(tab.type)"
          v-bind="getTabProps(tab)"
        />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { defineAsyncComponent, onMounted } from 'vue';
import { useRoute } from 'vue-router';
import { storeToRefs } from 'pinia';
import { useNoteStore, type TabState } from '@/api/notes';

const StarNotes = defineAsyncComponent(() => import('./StarNotes.vue'));
const CalendarNotes = defineAsyncComponent(() => import('./CalendarNotes.vue'));
const ListNotes = defineAsyncComponent(() => import('./ListNotes.vue'));

const route = useRoute();
const noteStore = useNoteStore();
const { tabs, activeTabId } = storeToRefs(noteStore);

const getTabComponent = (type: TabState['type']) => {
  if (type === 'calendar') return CalendarNotes;
  if (type === 'list') return ListNotes;
  return StarNotes;
};

const getTabProps = (tab: TabState) => {
  const baseProps = {
    tabId: tab.id
  };

  if (tab.type === 'planet') {
    return {
      ...baseProps,
      targetNoteId: tab.data?.noteId,
      graphMode: tab.data?.mode || 'planetary',
    };
  }
  return baseProps;
};

// Handle tab change
const handleTabChange = (name: any) => {
  noteStore.setActiveTab(name);
};

const handleTabRemove = (name: any) => {
  noteStore.removeTab(name);
};

// Initialize based on route or other logic if needed
onMounted(() => {
  const requestedTab = typeof route.query.tab === 'string' ? route.query.tab : null;
  noteStore.setActiveTab(requestedTab || 'calendar');
});
</script>

<style scoped>
.notes-center {
  padding: 20px;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  overflow: hidden;
  background-color: #f5f7fa;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  flex-shrink: 0;
}

.header h2 {
  margin: 0;
  color: #303133;
}

.notes-tabs {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0; /* Important for nested flex scrolling */
  background-color: white;
  border-radius: 8px;
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.1);
  padding: 0; /* We handle padding in header/content */
}

:deep(.el-tabs__header) {
  margin-bottom: 0;
  padding: 10px 10px 0 10px;
  background-color: #f5f7fa;
  border-bottom: 1px solid #e4e7ed;
}

:deep(.el-tabs__nav-wrap::after) {
  /* Remove default underline for card type if desired, but Element Plus handles card style */
  display: none;
}

:deep(.el-tabs__content) {
  flex: 1;
  padding: 0; /* Remove padding to let children control it */
  overflow: hidden; /* Prevent tab content from spilling */
  display: flex;
  flex-direction: column;
}

:deep(.el-tab-pane) {
  height: 100%;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
</style>
