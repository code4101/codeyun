<template>
  <div class="notes-center">
    <WorkspaceMenu :items="workspaceMenus" @select="selectMenu" />
    <DockWorkspace :dock="dock" mobile-tools content-scroll="hidden">
      <template #content-header>
        <EditorTabs :tabs="workspaceTabs" :active="activeTabId" label="日记视图"
          @activate="noteStore.setActiveTab" @close="noteStore.removeTab" @move="moveTab">
          <template #actions>
            <el-button class="filter-toggle" size="small" text :icon="Filter"
              :disabled="activeTab?.type !== 'calendar'" @click="dock.toggle('filters')">筛选</el-button>
          </template>
        </EditorTabs>
      </template>
      <template #views>
        <nav class="view-list" aria-label="日记视图">
          <button v-for="tab in tabs" :key="tab.id" type="button"
            :class="{ selected: activeTabId === tab.id }" :aria-current="activeTabId === tab.id ? 'page' : undefined"
            @click="noteStore.setActiveTab(tab.id)">{{ tab.label }}</button>
          <el-button text :icon="Delete" @click="router.push('/notes/trash')">回收站</el-button>
        </nav>
      </template>
      <template #filters>
        <p v-if="activeTab?.type !== 'calendar'" class="tool-hint">切换到日历视图后，可在这里调整筛选。</p>
        <div ref="filterTarget" class="calendar-filter-tool" v-show="activeTab?.type === 'calendar'" />
      </template>
      <div v-for="tab in mountedTabs" v-show="activeTabId === tab.id" :key="tab.id"
        class="notes-view" role="tabpanel" :aria-label="tab.label">
        <component :is="getTabComponent(tab.type)" v-bind="getTabProps(tab)" />
      </div>
    </DockWorkspace>
  </div>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { storeToRefs } from 'pinia';
import { Delete, Filter } from '@element-plus/icons-vue';
import { useNoteStore, type NoteProgramRule, type TabState } from '@/api/notes';
import DockWorkspace from '@/components/docking/DockWorkspace.vue';
import { useDockLayout } from '@/components/docking/useDockLayout';
import EditorTabs from '@/components/editor-workspace/EditorTabs.vue';
import WorkspaceMenu from '@/components/editor-workspace/WorkspaceMenu.vue';
import { dockWindowMenuItems, executeDockWindowCommand, type WorkspaceMenuItem } from '@/components/editor-workspace/workspaceMenu';
import { useUserStore } from '@/store/userStore';
import { markBootPerf, markBootPerfAsync } from '@/utils/bootPerf';

const StarNotes = defineAsyncComponent(() => import('./StarNotes.vue'));
const TimelineNotes = defineAsyncComponent(() => import('./TimelineNotes.vue'));
markBootPerf('notes-center.module');
const loadCalendarNotes = async () => {
  const perfEnabled = typeof window !== 'undefined' && new URLSearchParams(window.location.search).has('perf');
  const startedAt = perfEnabled ? performance.now() : 0;
  const component = await import('./CalendarNotes.vue');
  if (perfEnabled) {
    const duration = performance.now() - startedAt;
    ((window as any).__codeyunCalendarPerfEvents ||= []).push({ label: 'CalendarNotes import', duration });
    document.documentElement.setAttribute(
      'data-codeyun-calendar-perf',
      JSON.stringify((window as any).__codeyunCalendarPerfEvents),
    );
    console.info(`[NotesCenter perf] CalendarNotes import: ${duration.toFixed(1)}ms`);
  }
  return component;
};
const CalendarNotes = defineAsyncComponent(loadCalendarNotes);
const ListNotes = defineAsyncComponent(() => markBootPerfAsync('notes-center.list.import', () => import('./ListNotes.vue')));

const route = useRoute();
const router = useRouter();
const noteStore = useNoteStore();
const { tabs, activeTabId } = storeToRefs(noteStore);
const dock = useDockLayout(`codeyun.notes-center.dock:${useUserStore().user?.id ?? 'local'}`, [
  { id: 'views', title: '视图导航', icon: 'calendar', position: 'left' },
  { id: 'filters', title: '日历筛选', icon: 'search', position: 'right' },
]);
const filterTarget = ref<HTMLElement | null>(null);
const activeTab = computed(() => tabs.value.find(tab => tab.id === activeTabId.value));
const workspaceTabs = computed(() => tabs.value.map(tab => ({ id: tab.id, title: tab.label, closable: tab.closable })));
// 与旧 lazy 标签一致：首次激活后保留实例，切换视图不丢失选择、滚动和编辑状态。
const visitedTabs = ref(new Set<string>());
const mountedTabs = computed(() => tabs.value.filter(tab => visitedTabs.value.has(tab.id)));
const workspaceMenus = computed<WorkspaceMenuItem[]>(() => [
  { id: 'diary', label: '日记', children: [{ id: 'trash', label: '回收站' }] },
  { id: 'views', label: '视图', children: tabs.value.map(tab => ({ id: `view:${tab.id}`, label: `${activeTabId.value === tab.id ? '✓ ' : ''}${tab.label}` })) },
  { id: 'window', label: '窗口', children: [
    { id: 'tool:views', label: '视图导航' },
    { id: 'tool:filters', label: '日历筛选' },
    ...dockWindowMenuItems(dock),
  ] },
]);
function selectMenu(id: string) {
  if (executeDockWindowCommand(dock, id)) return;
  if (id === 'trash') void router.push('/notes/trash');
  else if (id.startsWith('view:')) noteStore.setActiveTab(id.slice(5));
  else if (id.startsWith('tool:')) dock.open(id.slice(5));
}
function moveTab(id: string, before: string) {
  if (id === before) return;
  const source = tabs.value.findIndex(tab => tab.id === id);
  if (source < 0 || !tabs.value.some(tab => tab.id === before)) return;
  const [tab] = tabs.value.splice(source, 1);
  tabs.value.splice(tabs.value.findIndex(tab => tab.id === before), 0, tab!);
}


const getTabComponent = (type: TabState['type']) => {
  if (type === 'timeline') return TimelineNotes;
  if (type === 'calendar') return CalendarNotes;
  if (type === 'list') return ListNotes;
  return StarNotes;
};

const excludeWechatDailyRules: NoteProgramRule[] = [
  {
    action: 'exclude',
    matcher: {
      kind: 'field',
      field: 'custom_fields.wechat_daily_source',
      op: 'eq',
      value: 'mf:v4_db_storage',
    },
  },
];

const getTabProps = (tab: TabState) => {
  const baseProps = {
    tabId: tab.id,
    active: activeTabId.value === tab.id
  };

  if (tab.type === 'calendar') {
    return {
      ...baseProps,
      workbench: true,
      filterTarget: filterTarget.value,
      dataFilterRules: excludeWechatDailyRules,
      fixedViewFilterRules: excludeWechatDailyRules,
    };
  }

  if (tab.type === 'planet') {
    return {
      ...baseProps,
      targetNoteId: tab.data?.noteId,
      graphMode: tab.data?.mode || 'planetary',
    };
  }
  return baseProps;
};

watch(
  () => route.query.tab,
  (tab) => {
    noteStore.setActiveTab(typeof tab === 'string' ? tab : 'calendar');
  },
  { immediate: true },
);
watch(activeTabId, id => { visitedTabs.value.add(id); }, { immediate: true });
watch(activeTabId, id => {
  if (route.query.tab !== id) void router.replace({ query: { ...route.query, tab: id } });
});
</script>

<style scoped>
.notes-center {
  --reader-panel: var(--el-fill-color-light);
  --reader-content: var(--el-bg-color);
  --reader-text: var(--el-text-color-primary);
  --reader-border: var(--el-border-color-light);
  --reader-active-text: var(--el-color-primary);
  --reader-hover: var(--el-fill-color);
  display: flex; flex-direction: column; height: 100%; min-height: 0; min-width: 0;
  overflow: hidden; background: var(--reader-content);
}
.notes-view { display: flex; flex-direction: column; flex: 1; height: 100%; min-height: 0; min-width: 0; overflow: hidden; }
.filter-toggle { margin: 4px 8px; }
.view-list { display: flex; flex-direction: column; gap: 4px; padding: 8px; }
.view-list > button { text-align: left; border: 0; border-radius: 4px; padding: 9px 12px; cursor: pointer; color: var(--reader-text); background: transparent; font: inherit; font-size: 13px; }
.view-list > button:hover, .view-list > button.selected { color: var(--reader-active-text); background: var(--reader-hover); }
.calendar-filter-tool { padding: 8px; min-width: 0; }
.tool-hint { padding: 12px; color: var(--el-text-color-secondary); font-size: 12px; line-height: 1.6; }
</style>
