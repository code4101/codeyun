<script setup lang="ts">
import { ref, computed, nextTick, onBeforeUnmount, onMounted, watch } from 'vue';
import type { ComponentPublicInstance } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  findPermissionKeyByMenuPath,
  requirePermissionTitle,
  requirePermissionTitleByMenuPath,
} from '@/features/access/permissionRegistry';
import {
  findPluginMenuIndex,
  getDefaultPluginOpeneds,
  isPluginMenuItemVisible,
  pluginMenuSections,
} from '@/plugins';
import {
  getMatchedMenuPath,
  requirePageCanonicalPath,
  requirePageMenuPath,
} from '@/router/pageRegistry';
import { buildStandaloneRouteLocation } from '@/router/standalone';
import { useFeatureAccessStore } from '@/store/featureAccessStore';
import { useUserStore } from '@/store/userStore';
import {
  Calendar,
  Document,
  Menu as IconMenu,
  // Location,
  Monitor,
  User,
  SwitchButton,
  // Cellphone,
  MagicStick,
  Box,
  ChatDotRound,
  Expand,
  Fold,
  InfoFilled,
  Setting
} from '@element-plus/icons-vue';

const route = useRoute();
const router = useRouter();
const featureAccessStore = useFeatureAccessStore();
const userStore = useUserStore();
const isCollapse = ref(false);
const isResizingAside = ref(false);
const asideRef = ref<HTMLElement | ComponentPublicInstance | null>(null);
const COLLAPSED_ASIDE_WIDTH = 64;
const MIN_EXPANDED_ASIDE_WIDTH = 200;
const MAX_EXPANDED_ASIDE_WIDTH = 420;
const ASIDE_WIDTH_STORAGE_KEY = 'layout.mainAsideWidthPx';
const expandedAsideWidthPx = ref(MIN_EXPANDED_ASIDE_WIDTH);
const manualExpandedAsideWidthPx = ref<number | null>(null);
const asideWidth = computed(() => (
  isCollapse.value
    ? `${COLLAPSED_ASIDE_WIDTH}px`
    : `${(manualExpandedAsideWidthPx.value ?? expandedAsideWidthPx.value)}px`
));
let pendingAsideMeasureFrame = 0;
const menuRenderKey = ref(0);
const lastMenuPointerIntent = ref<{
  modified: boolean;
  time: number;
} | null>(null);
const HOME_PATH = requirePageMenuPath('Home');
const PASSWORD_GENERATOR_PATH = requirePageMenuPath('PasswordGenerator');
const IMAGE_BROWSER_PATH = requirePageMenuPath('ImageBrowser');
const COLOR_TOOLS_PATH = requirePageMenuPath('ColorTools');
const AI_CONFIG_PATH = requirePageMenuPath('AiConfig');
const AI_CODEX_SAVER_PATH = requirePageMenuPath('AiCodexSaver');
const AI_EVOMIND_PATH = requirePageMenuPath('AiEvoMind');
const AI_CHAT_PATH = requirePageMenuPath('AiChat');
const AI_REDUCTION_PATH = requirePageMenuPath('AiReduction');
const AI_GIT_COMMIT_PATH = requirePageMenuPath('AiGitCommit');
const AI_NOTEBOOK_PATH = requirePageMenuPath('AiNotebook');
const AI_WECHAT_PATH = requirePageMenuPath('AiWechat');
const ATTENDANCE_CONFIGS_PATH = requirePageMenuPath('AttendanceConfigs');
const ATTENDANCE_HEADER_TOOL_PATH = requirePageMenuPath('AttendanceHeaderTool');
const ATTENDANCE_WJX_COLLECT_PATH = requirePageMenuPath('AttendanceWjxCollect');
const ATTENDANCE_ORDERS_PATH = requirePageMenuPath('AttendanceOrders');
const DSP_CALCULATOR_PATH = requirePageMenuPath('DspCalculator');
const MAGIC_CRAFT_XOR_MATRIX_PATH = requirePageMenuPath('XorMatrix');
const FANXIU_CALCULATOR_PATH = requirePageMenuPath('BeastSoulCalculator');
const FANXIU_DRAW_CALC_PATH = requirePageMenuPath('DrawCalculator');
const FANXIU_LOTTERY_MODEL_PATH = requirePageMenuPath('FanxiuLotteryModel');
const FANXIU_DISCOUNT_PATH = requirePageMenuPath('FanxiuDiscountGuide');
const FANXIU_TASK_STATUS_PATH = requirePageMenuPath('FanxiuTaskStatus');
const FANXIU_ACTIVITY_LIST_PATH = requirePageMenuPath('FanxiuActivityList');
const FANXIU_ACTIVITY_LIST_KUNLUN_SECRET_PATH = requirePageMenuPath('FanxiuKunlunSecret');
const FANXIU_ACTIVITY_LIST_MODAO_INVASION_PATH = requirePageMenuPath('FanxiuModaoInvasion');
const FANXIU_ACTIVITY_LIST_SHOUYUAN_EXPLORATION_PATH = requirePageMenuPath('FanxiuShouyuanExploration');
const FANXIU_ACTIVITY_LIST_DIVINE_RESOURCE_PATH = requirePageMenuPath('FanxiuDivineResource');
const FANXIU_ACTIVITY_LIST_XIANZHOU_MARATHON_PATH = requirePageMenuPath('FanxiuXianzhouMarathon');
const FANXIU_REGION_DATA_PATH = requirePageMenuPath('FanxiuRegionData');
const FANXIU_WARDROBE_HALL_PATH = requirePageMenuPath('FanxiuWardrobeHall');
const FANXIU_SPIRIT_BEAST_HALL_PATH = requirePageMenuPath('FanxiuSpiritBeastHall');
const FANXIU_MAGIC_TREASURE_HALL_PATH = requirePageMenuPath('FanxiuMagicTreasureHall');
const FANXIU_MAGIC_TREASURE_FORMATIONS_PATH = requirePageMenuPath('FanxiuMagicTreasureFormations');
const FANXIU_LABELME_PATH = requirePageMenuPath('FanxiuLabelmeBrowser');
const FANXIU_RECHARGE_PATH = requirePageMenuPath('FanxiuRecharge');
const FANXIU_CUIJIAN_TRIAL_PATH = requirePageMenuPath('CuijianTrial');
const NOTES_CENTER_MENU_PATH = requirePageMenuPath('NotesCenter');
const EASTMONEY_PATH = requirePageMenuPath('Eastmoney');
const FREEBILL_PATH = requirePageMenuPath('Freebill');
const NOTES_SHEETS_MANAGER_PATH = requirePageMenuPath('NotesSheetManager');
const NOTES_WECHAT_PATH = requirePageMenuPath('NotesWechat');
const NOTES_INFINITE_CANVAS_PATH = requirePageMenuPath('InfiniteCanvas');
const CLUSTER_TASKS_PATH = requirePageMenuPath('DeviceTasks');
const CLUSTER_FILES_PATH = requirePageMenuPath('DeviceFileBrowser');
const CLUSTER_STORAGE_PATH = requirePageMenuPath('ClusterStorageManager');
const CLUSTER_CODEX_PATH = requirePageMenuPath('ClusterCodexSessions');
const CLUSTER_VIEW_MN_PATH = requirePageMenuPath('ClusterViewMn');
const CLUSTER_LABELME_PATH = requirePageMenuPath('DeviceLabelmeBrowser');
const CLUSTER_FILES_SUBMENU_INDEX = 'cluster-files';
const FANXIU_ACTIVITY_LIST_SUBMENU_INDEX = 'fanxiu-activity-list';
const FANXIU_MAGIC_TREASURE_SUBMENU_INDEX = 'fanxiu-magic-treasure';
const ADMIN_ACCOUNTS_PATH = requirePageMenuPath('AccountManager');
const ADMIN_IMAGES_PATH = requirePageMenuPath('StorageManager');
const ADMIN_BACKGROUND_TASKS_PATH = requirePageMenuPath('BackgroundTasks');
const ATTENDANCE_PATH_PREFIX = requirePageCanonicalPath('AttendanceConfigs').split('/configs')[0];
const HOME_TITLE = requirePermissionTitle('home');
const TOOLS_TITLE = requirePermissionTitle('tools');
const PASSWORD_GENERATOR_TITLE = requirePermissionTitleByMenuPath(PASSWORD_GENERATOR_PATH);
const IMAGE_BROWSER_TITLE = requirePermissionTitleByMenuPath(IMAGE_BROWSER_PATH);
const COLOR_TOOLS_TITLE = requirePermissionTitleByMenuPath(COLOR_TOOLS_PATH);
const AI_TOOLS_TITLE = requirePermissionTitle('ai-tools');
const AI_CONFIG_TITLE = requirePermissionTitleByMenuPath(AI_CONFIG_PATH);
const AI_CODEX_SAVER_TITLE = requirePermissionTitleByMenuPath(AI_CODEX_SAVER_PATH);
const AI_EVOMIND_TITLE = requirePermissionTitleByMenuPath(AI_EVOMIND_PATH);
const AI_CHAT_TITLE = requirePermissionTitleByMenuPath(AI_CHAT_PATH);
const AI_REDUCTION_TITLE = requirePermissionTitleByMenuPath(AI_REDUCTION_PATH);
const AI_GIT_COMMIT_TITLE = requirePermissionTitleByMenuPath(AI_GIT_COMMIT_PATH);
const AI_NOTEBOOK_TITLE = requirePermissionTitleByMenuPath(AI_NOTEBOOK_PATH);
const AI_WECHAT_TITLE = requirePermissionTitleByMenuPath(AI_WECHAT_PATH);
const ATTENDANCE_TOOLS_TITLE = requirePermissionTitle('attendance-tools');
const ATTENDANCE_CONFIGS_TITLE = requirePermissionTitleByMenuPath(ATTENDANCE_CONFIGS_PATH);
const ATTENDANCE_HEADER_TOOL_TITLE = requirePermissionTitleByMenuPath(ATTENDANCE_HEADER_TOOL_PATH);
const ATTENDANCE_WJX_COLLECT_TITLE = requirePermissionTitleByMenuPath(ATTENDANCE_WJX_COLLECT_PATH);
const ATTENDANCE_ORDERS_TITLE = requirePermissionTitleByMenuPath(ATTENDANCE_ORDERS_PATH);
const GAME_TOOLS_TITLE = requirePermissionTitle('game-tools');
const DSP_CALCULATOR_TITLE = requirePermissionTitleByMenuPath(DSP_CALCULATOR_PATH);
const MAGIC_CRAFT_TITLE = requirePermissionTitle('magic-craft');
const MAGIC_CRAFT_XOR_MATRIX_TITLE = requirePermissionTitleByMenuPath(MAGIC_CRAFT_XOR_MATRIX_PATH);
const FANXIU_TITLE = requirePermissionTitle('fanxiu');
const FANXIU_CALCULATOR_TITLE = requirePermissionTitleByMenuPath(FANXIU_CALCULATOR_PATH);
const FANXIU_DRAW_CALC_TITLE = requirePermissionTitleByMenuPath(FANXIU_DRAW_CALC_PATH);
const FANXIU_LOTTERY_MODEL_TITLE = requirePermissionTitleByMenuPath(FANXIU_LOTTERY_MODEL_PATH);
const FANXIU_DISCOUNT_TITLE = requirePermissionTitleByMenuPath(FANXIU_DISCOUNT_PATH);
const FANXIU_TASK_STATUS_TITLE = requirePermissionTitleByMenuPath(FANXIU_TASK_STATUS_PATH);
const FANXIU_ACTIVITY_LIST_TITLE = requirePermissionTitleByMenuPath(FANXIU_ACTIVITY_LIST_PATH);
const FANXIU_ACTIVITY_LIST_KUNLUN_SECRET_TITLE = requirePermissionTitleByMenuPath(FANXIU_ACTIVITY_LIST_KUNLUN_SECRET_PATH);
const FANXIU_ACTIVITY_LIST_MODAO_INVASION_TITLE = requirePermissionTitleByMenuPath(FANXIU_ACTIVITY_LIST_MODAO_INVASION_PATH);
const FANXIU_ACTIVITY_LIST_SHOUYUAN_EXPLORATION_TITLE = requirePermissionTitleByMenuPath(FANXIU_ACTIVITY_LIST_SHOUYUAN_EXPLORATION_PATH);
const FANXIU_ACTIVITY_LIST_DIVINE_RESOURCE_TITLE = requirePermissionTitleByMenuPath(FANXIU_ACTIVITY_LIST_DIVINE_RESOURCE_PATH);
const FANXIU_ACTIVITY_LIST_XIANZHOU_MARATHON_TITLE = requirePermissionTitleByMenuPath(FANXIU_ACTIVITY_LIST_XIANZHOU_MARATHON_PATH);
const FANXIU_REGION_DATA_TITLE = requirePermissionTitleByMenuPath(FANXIU_REGION_DATA_PATH);
const FANXIU_INVENTORY_TITLE = requirePermissionTitle('fanxiu.inventory');
const FANXIU_WARDROBE_HALL_TITLE = requirePermissionTitleByMenuPath(FANXIU_WARDROBE_HALL_PATH);
const FANXIU_SPIRIT_BEAST_HALL_TITLE = requirePermissionTitleByMenuPath(FANXIU_SPIRIT_BEAST_HALL_PATH);
const FANXIU_MAGIC_TREASURE_HALL_TITLE = requirePermissionTitleByMenuPath(FANXIU_MAGIC_TREASURE_HALL_PATH);
const FANXIU_MAGIC_TREASURE_FORMATIONS_TITLE = requirePermissionTitleByMenuPath(FANXIU_MAGIC_TREASURE_FORMATIONS_PATH);
const FANXIU_LABELME_TITLE = requirePermissionTitleByMenuPath(FANXIU_LABELME_PATH);
const FANXIU_RECHARGE_TITLE = requirePermissionTitleByMenuPath(FANXIU_RECHARGE_PATH);
const FANXIU_CUIJIAN_TRIAL_TITLE = requirePermissionTitleByMenuPath(FANXIU_CUIJIAN_TRIAL_PATH);
const NOTE_TOOLS_TITLE = requirePermissionTitle('note-tools');
const NOTES_CENTER_TITLE = requirePermissionTitleByMenuPath(NOTES_CENTER_MENU_PATH);
const EASTMONEY_TITLE = requirePermissionTitleByMenuPath(EASTMONEY_PATH);
const FREEBILL_TITLE = requirePermissionTitleByMenuPath(FREEBILL_PATH);
const NOTES_SHEETS_MANAGER_TITLE = requirePermissionTitleByMenuPath(NOTES_SHEETS_MANAGER_PATH);
const NOTES_WECHAT_TITLE = requirePermissionTitleByMenuPath(NOTES_WECHAT_PATH);
const NOTES_INFINITE_CANVAS_TITLE = requirePermissionTitleByMenuPath(NOTES_INFINITE_CANVAS_PATH);
const CLUSTER_TOOLS_TITLE = requirePermissionTitle('cluster-tools');
const CLUSTER_TASKS_TITLE = requirePermissionTitleByMenuPath(CLUSTER_TASKS_PATH);
const CLUSTER_FILES_TITLE = requirePermissionTitleByMenuPath(CLUSTER_FILES_PATH);
const CLUSTER_STORAGE_TITLE = requirePermissionTitleByMenuPath(CLUSTER_STORAGE_PATH);
const CLUSTER_CODEX_TITLE = requirePermissionTitleByMenuPath(CLUSTER_CODEX_PATH);
const CLUSTER_VIEW_MN_TITLE = requirePermissionTitleByMenuPath(CLUSTER_VIEW_MN_PATH);
const CLUSTER_LABELME_TITLE = requirePermissionTitleByMenuPath(CLUSTER_LABELME_PATH);
const ADMIN_TOOLS_TITLE = requirePermissionTitle('admin-tools');
const ADMIN_ACCOUNTS_TITLE = requirePermissionTitleByMenuPath(ADMIN_ACCOUNTS_PATH);
const ADMIN_IMAGES_TITLE = requirePermissionTitleByMenuPath(ADMIN_IMAGES_PATH);
const ADMIN_BACKGROUND_TASKS_TITLE = requirePermissionTitleByMenuPath(ADMIN_BACKGROUND_TASKS_PATH);
const BUILTIN_MENU_SECTION_KEYS = new Set([
  'tools',
  'ai-tools',
  'attendance-tools',
  'game-tools',
  'fanxiu',
  'magic-craft',
  'note-tools',
  'cluster-tools',
  'admin-tools',
]);

const toggleCollapse = () => {
  isCollapse.value = !isCollapse.value;
};

const getExpandedAsideViewportMaxWidth = () => Math.min(
  MAX_EXPANDED_ASIDE_WIDTH,
  Math.max(MIN_EXPANDED_ASIDE_WIDTH, Math.floor(window.innerWidth * 0.72)),
);

const clampExpandedAsideWidth = (width: number) => Math.min(
  Math.max(Math.round(width), MIN_EXPANDED_ASIDE_WIDTH),
  getExpandedAsideViewportMaxWidth(),
);

const persistManualAsideWidth = (width: number | null) => {
  if (typeof window === 'undefined') {
    return;
  }

  if (width == null) {
    window.localStorage.removeItem(ASIDE_WIDTH_STORAGE_KEY);
    return;
  }

  window.localStorage.setItem(ASIDE_WIDTH_STORAGE_KEY, String(width));
};

const applyManualAsideWidth = (width: number) => {
  const nextWidth = clampExpandedAsideWidth(width);
  manualExpandedAsideWidthPx.value = nextWidth;
  persistManualAsideWidth(nextWidth);
};

const getAsideElement = () => {
  const rawAside = asideRef.value;
  if (!rawAside) {
    return null;
  }

  if (rawAside instanceof HTMLElement) {
    return rawAside;
  }

  const componentRoot = (rawAside as ComponentPublicInstance).$el;
  return componentRoot instanceof HTMLElement ? componentRoot : null;
};

const measureExpandedAsideWidth = () => {
  if (isCollapse.value || manualExpandedAsideWidthPx.value != null) {
    return;
  }

  const asideElement = getAsideElement();
  if (!asideElement) {
    return;
  }

  const menuEntries = Array.from(
    asideElement.querySelectorAll<HTMLElement>('.el-menu-item, .el-sub-menu__title'),
  ).filter((element) => element.offsetParent !== null);

  if (!menuEntries.length) {
    expandedAsideWidthPx.value = MIN_EXPANDED_ASIDE_WIDTH;
    return;
  }

  const contentWidth = menuEntries.reduce(
    (maxWidth, element) => Math.max(maxWidth, Math.ceil(element.scrollWidth)),
    0,
  );
  const nextWidth = Math.min(
    Math.max(contentWidth + 12, MIN_EXPANDED_ASIDE_WIDTH),
    getExpandedAsideViewportMaxWidth(),
  );

  expandedAsideWidthPx.value = nextWidth;
};

const scheduleAsideWidthMeasure = () => {
  if (typeof window === 'undefined') {
    return;
  }

  if (pendingAsideMeasureFrame) {
    window.cancelAnimationFrame(pendingAsideMeasureFrame);
  }

  pendingAsideMeasureFrame = window.requestAnimationFrame(() => {
    pendingAsideMeasureFrame = 0;
    void nextTick(() => {
      measureExpandedAsideWidth();
    });
  });
};

const activeMenu = computed(() => {
  const matchedMenuPath = getMatchedMenuPath(route);
  if (matchedMenuPath === CLUSTER_FILES_PATH) return CLUSTER_FILES_SUBMENU_INDEX;
  if (matchedMenuPath === FANXIU_ACTIVITY_LIST_PATH) return FANXIU_ACTIVITY_LIST_SUBMENU_INDEX;
  if (matchedMenuPath === FANXIU_MAGIC_TREASURE_HALL_PATH) return FANXIU_MAGIC_TREASURE_SUBMENU_INDEX;
  if (matchedMenuPath) return matchedMenuPath;
  const pluginMenuIndex = findPluginMenuIndex(route.path);
  if (pluginMenuIndex) return pluginMenuIndex;
  return route.path;
});

const canAccessFeature = (key: string) => featureAccessStore.isAllowed(key);

const canAccessMenuPath = (path: string) => {
  const permissionKey = findPermissionKeyByMenuPath(path);
  if (!permissionKey) {
    return false;
  }
  return featureAccessStore.isAllowed(permissionKey);
};

const visiblePluginMenuSections = computed(() =>
  pluginMenuSections
    .map((section) => ({
      ...section,
      items: section.items.filter((item) =>
        isPluginMenuItemVisible(item, userStore.isAuthenticated, userStore.isAdmin)
        && canAccessMenuPath(item.path),
      ),
    }))
    .filter((section) => canAccessFeature(section.permissionKey ?? section.key) && section.items.length > 0),
);

const visibleStandalonePluginMenuSections = computed(() =>
  visiblePluginMenuSections.value.filter((section) => !BUILTIN_MENU_SECTION_KEYS.has(section.key)),
);

const clusterPluginMenuItems = computed(() =>
  visiblePluginMenuSections.value
    .filter((section) => section.key === 'cluster-tools')
    .flatMap((section) => section.items),
);

const toolsMenuVisible = computed(() =>
  canAccessFeature('tools')
  && [
    PASSWORD_GENERATOR_PATH,
    IMAGE_BROWSER_PATH,
    COLOR_TOOLS_PATH,
  ].some((path) => canAccessMenuPath(path)),
);

const aiToolsMenuVisible = computed(() =>
  canAccessFeature('ai-tools')
  && [
    AI_CONFIG_PATH,
    AI_CODEX_SAVER_PATH,
    AI_EVOMIND_PATH,
    CLUSTER_CODEX_PATH,
    AI_CHAT_PATH,
    AI_REDUCTION_PATH,
    AI_GIT_COMMIT_PATH,
    AI_NOTEBOOK_PATH,
    AI_WECHAT_PATH,
  ].some((path) => canAccessMenuPath(path)),
);

const attendanceMenuVisible = computed(() =>
  canAccessFeature('attendance-tools')
  && [
    ATTENDANCE_CONFIGS_PATH,
    ATTENDANCE_HEADER_TOOL_PATH,
    ATTENDANCE_WJX_COLLECT_PATH,
    ATTENDANCE_ORDERS_PATH,
  ].some((path) => canAccessMenuPath(path)),
);

const fanxiuMenuVisible = computed(() =>
  canAccessFeature('fanxiu')
  && [
    FANXIU_TASK_STATUS_PATH,
    FANXIU_ACTIVITY_LIST_PATH,
    FANXIU_ACTIVITY_LIST_KUNLUN_SECRET_PATH,
    FANXIU_ACTIVITY_LIST_MODAO_INVASION_PATH,
    FANXIU_ACTIVITY_LIST_SHOUYUAN_EXPLORATION_PATH,
    FANXIU_ACTIVITY_LIST_DIVINE_RESOURCE_PATH,
    FANXIU_ACTIVITY_LIST_XIANZHOU_MARATHON_PATH,
    FANXIU_REGION_DATA_PATH,
    FANXIU_WARDROBE_HALL_PATH,
    FANXIU_SPIRIT_BEAST_HALL_PATH,
    FANXIU_MAGIC_TREASURE_HALL_PATH,
    FANXIU_MAGIC_TREASURE_FORMATIONS_PATH,
    FANXIU_LABELME_PATH,
    FANXIU_CALCULATOR_PATH,
    FANXIU_DRAW_CALC_PATH,
    FANXIU_LOTTERY_MODEL_PATH,
    FANXIU_DISCOUNT_PATH,
    FANXIU_RECHARGE_PATH,
    FANXIU_CUIJIAN_TRIAL_PATH,
  ].some((path) => canAccessMenuPath(path)),
);

const fanxiuActivityListMenuVisible = computed(() =>
  canAccessFeature('fanxiu.activity-list')
  && [
    FANXIU_ACTIVITY_LIST_PATH,
    FANXIU_ACTIVITY_LIST_KUNLUN_SECRET_PATH,
    FANXIU_ACTIVITY_LIST_MODAO_INVASION_PATH,
    FANXIU_ACTIVITY_LIST_SHOUYUAN_EXPLORATION_PATH,
    FANXIU_ACTIVITY_LIST_DIVINE_RESOURCE_PATH,
    FANXIU_ACTIVITY_LIST_XIANZHOU_MARATHON_PATH,
  ].some((path) => canAccessMenuPath(path)),
);

const fanxiuInventoryMenuVisible = computed(() =>
  canAccessFeature('fanxiu.inventory')
  && [
    FANXIU_WARDROBE_HALL_PATH,
    FANXIU_SPIRIT_BEAST_HALL_PATH,
    FANXIU_MAGIC_TREASURE_HALL_PATH,
    FANXIU_MAGIC_TREASURE_FORMATIONS_PATH,
  ].some((path) => canAccessMenuPath(path)),
);

const fanxiuMagicTreasureMenuVisible = computed(() =>
  [FANXIU_MAGIC_TREASURE_HALL_PATH, FANXIU_MAGIC_TREASURE_FORMATIONS_PATH].some((path) => canAccessMenuPath(path)),
);

const magicCraftMenuVisible = computed(() =>
  canAccessFeature('magic-craft')
  && canAccessMenuPath(MAGIC_CRAFT_XOR_MATRIX_PATH),
);

const gameToolsMenuVisible = computed(() =>
  canAccessFeature('game-tools')
  && (
    fanxiuMenuVisible.value
    || magicCraftMenuVisible.value
    || canAccessMenuPath(DSP_CALCULATOR_PATH)
  ),
);

const noteToolsMenuVisible = computed(() =>
  canAccessFeature('note-tools')
  && [
    NOTES_CENTER_MENU_PATH,
    EASTMONEY_PATH,
    FREEBILL_PATH,
    NOTES_SHEETS_MANAGER_PATH,
    NOTES_WECHAT_PATH,
    NOTES_INFINITE_CANVAS_PATH,
  ].some((path) => canAccessMenuPath(path)),
);

const clusterFilesMenuVisible = computed(() =>
  canAccessMenuPath(CLUSTER_FILES_PATH) || canAccessMenuPath(CLUSTER_VIEW_MN_PATH),
);

const clusterFilesMenuEntryPath = computed(() =>
  canAccessMenuPath(CLUSTER_FILES_PATH) ? CLUSTER_FILES_PATH : CLUSTER_VIEW_MN_PATH,
);

const fanxiuActivityListMenuEntryPath = computed(() =>
  canAccessMenuPath(FANXIU_ACTIVITY_LIST_PATH)
    ? FANXIU_ACTIVITY_LIST_PATH
    : canAccessMenuPath(FANXIU_ACTIVITY_LIST_KUNLUN_SECRET_PATH)
      ? FANXIU_ACTIVITY_LIST_KUNLUN_SECRET_PATH
      : canAccessMenuPath(FANXIU_ACTIVITY_LIST_MODAO_INVASION_PATH)
        ? FANXIU_ACTIVITY_LIST_MODAO_INVASION_PATH
        : canAccessMenuPath(FANXIU_ACTIVITY_LIST_SHOUYUAN_EXPLORATION_PATH)
          ? FANXIU_ACTIVITY_LIST_SHOUYUAN_EXPLORATION_PATH
          : canAccessMenuPath(FANXIU_ACTIVITY_LIST_DIVINE_RESOURCE_PATH)
            ? FANXIU_ACTIVITY_LIST_DIVINE_RESOURCE_PATH
            : FANXIU_ACTIVITY_LIST_XIANZHOU_MARATHON_PATH,
);

const fanxiuMagicTreasureMenuEntryPath = computed(() =>
  canAccessMenuPath(FANXIU_MAGIC_TREASURE_HALL_PATH)
    ? FANXIU_MAGIC_TREASURE_HALL_PATH
    : FANXIU_MAGIC_TREASURE_FORMATIONS_PATH,
);

const clusterMenuVisible = computed(() =>
  canAccessFeature('cluster-tools')
  && (
    [
      CLUSTER_TASKS_PATH,
      CLUSTER_STORAGE_PATH,
      CLUSTER_LABELME_PATH,
    ].some((path) => canAccessMenuPath(path))
    || clusterFilesMenuVisible.value
    || clusterPluginMenuItems.value.length > 0
  ),
);

const adminMenuVisible = computed(() =>
  userStore.isAdmin
  && canAccessFeature('admin-tools')
  && [
    ADMIN_ACCOUNTS_PATH,
    ADMIN_IMAGES_PATH,
    ADMIN_BACKGROUND_TASKS_PATH,
  ].some((path) => canAccessMenuPath(path)),
);

const defaultOpeneds = computed(() => {
  const openeds: string[] = [];
  if (route.path === ATTENDANCE_PATH_PREFIX) return ['attendance-tools'];
  if (route.path === '/cluster') return ['cluster-tools'];
  if (route.path === CLUSTER_CODEX_PATH || route.path.startsWith(`${CLUSTER_CODEX_PATH}/`)) {
    openeds.push('ai-tools');
  } else if (route.path.startsWith('/cluster/')) {
    openeds.push('cluster-tools');
  }
  if ([CLUSTER_FILES_PATH, CLUSTER_VIEW_MN_PATH].some((path) => route.path === path || route.path.startsWith(`${path}/`))) {
    openeds.push(CLUSTER_FILES_SUBMENU_INDEX);
  }
  if (route.path.startsWith('/admin/')) openeds.push('admin-tools');
  if (route.path.startsWith('/tools/ai-')) openeds.push('ai-tools');
  if (route.path.startsWith(ATTENDANCE_PATH_PREFIX)) openeds.push('attendance-tools');
  if (route.path.startsWith('/tools/')) openeds.push('tools');
  if (route.path.startsWith('/fanxiu/')) openeds.push('game-tools', 'fanxiu');
  if (route.path === FANXIU_ACTIVITY_LIST_PATH || route.path.startsWith(`${FANXIU_ACTIVITY_LIST_PATH}/`)) {
    openeds.push(FANXIU_ACTIVITY_LIST_SUBMENU_INDEX);
  }
  if (route.path.startsWith('/fanxiu/inventory/')) openeds.push('fanxiu-inventory');
  if (route.path === FANXIU_MAGIC_TREASURE_HALL_PATH || route.path.startsWith(`${FANXIU_MAGIC_TREASURE_HALL_PATH}/`)) {
    openeds.push(FANXIU_MAGIC_TREASURE_SUBMENU_INDEX);
  }
  if (route.path.startsWith('/magic-craft/')) openeds.push('game-tools', 'magic-craft');
  if (route.path.startsWith('/dsp/')) openeds.push('game-tools');
  if (route.path.startsWith('/notes/')) openeds.push('note-tools');
  openeds.push(...getDefaultPluginOpeneds(route.path));
  return Array.from(new Set(openeds));
});

const handleLogout = () => {
  userStore.logout();
  router.push('/login');
};

const handleLogin = () => {
  router.push('/login');
};

const standaloneRouteTarget = computed(() => buildStandaloneRouteLocation(route));
const standaloneRouteHref = computed(() => (
  standaloneRouteTarget.value
    ? router.resolve(standaloneRouteTarget.value).href
    : ''
));

const openMenuPathInNewTab = (path: string) => {
  window.open(router.resolve(path).href, '_blank', 'noopener,noreferrer');
};

const navigateMenuPath = (path: string) => {
  if (route.path !== path) {
    void router.push(path);
  }
};

const restoreMenuActiveState = () => {
  void nextTick(() => {
    menuRenderKey.value += 1;
  });
};

const recordMenuPointerIntent = (event: MouseEvent) => {
  if (!(event.target instanceof Element) || !event.target.closest('.el-menu-item')) {
    return;
  }
  lastMenuPointerIntent.value = {
    modified: event.ctrlKey || event.metaKey,
    time: Date.now(),
  };
};

const consumeMenuPointerIntent = () => {
  const intent = lastMenuPointerIntent.value;
  lastMenuPointerIntent.value = null;
  if (!intent || Date.now() - intent.time > 1500) {
    return null;
  }
  return intent;
};

const handleMenuSelect = (index: string) => {
  const intent = consumeMenuPointerIntent();
  if (intent?.modified) {
    openMenuPathInNewTab(index);
    restoreMenuActiveState();
    return;
  }
  navigateMenuPath(index);
};

const handleMenuTitleNavigate = (path: string, event?: MouseEvent) => {
  if (event?.ctrlKey || event?.metaKey) {
    openMenuPathInNewTab(path);
    return;
  }
  navigateMenuPath(path);
};

const resetAsideWidth = () => {
  manualExpandedAsideWidthPx.value = null;
  persistManualAsideWidth(null);
  scheduleAsideWidthMeasure();
};

const handleAsideResizeMove = (event: MouseEvent) => {
  applyManualAsideWidth(event.clientX);
};

const stopAsideResize = () => {
  if (!isResizingAside.value) {
    return;
  }
  isResizingAside.value = false;
  document.body.style.userSelect = '';
  window.removeEventListener('mousemove', handleAsideResizeMove);
  window.removeEventListener('mouseup', stopAsideResize);
};

const startAsideResize = (event: MouseEvent) => {
  if (isCollapse.value) {
    return;
  }

  event.preventDefault();
  isResizingAside.value = true;
  document.body.style.userSelect = 'none';
  window.addEventListener('mousemove', handleAsideResizeMove);
  window.addEventListener('mouseup', stopAsideResize);
};

const handleMenuStructureChange = () => {
  scheduleAsideWidthMeasure();
};

const handleWindowResize = () => {
  if (manualExpandedAsideWidthPx.value != null) {
    manualExpandedAsideWidthPx.value = clampExpandedAsideWidth(manualExpandedAsideWidthPx.value);
    persistManualAsideWidth(manualExpandedAsideWidthPx.value);
    return;
  }
  scheduleAsideWidthMeasure();
};

onMounted(() => {
  const rawStoredWidth = window.localStorage.getItem(ASIDE_WIDTH_STORAGE_KEY);
  if (rawStoredWidth) {
    const parsedWidth = Number(rawStoredWidth);
    if (Number.isFinite(parsedWidth)) {
      manualExpandedAsideWidthPx.value = clampExpandedAsideWidth(parsedWidth);
      persistManualAsideWidth(manualExpandedAsideWidthPx.value);
    } else {
      persistManualAsideWidth(null);
    }
  }
  scheduleAsideWidthMeasure();
  window.addEventListener('resize', handleWindowResize);
});

onBeforeUnmount(() => {
  stopAsideResize();
  if (pendingAsideMeasureFrame) {
    window.cancelAnimationFrame(pendingAsideMeasureFrame);
  }
  window.removeEventListener('resize', handleWindowResize);
});

watch(
  [
    activeMenu,
    defaultOpeneds,
    isCollapse,
    () => featureAccessStore.loaded,
    () => userStore.isAuthenticated,
    () => userStore.isAdmin,
  ],
  () => {
    scheduleAsideWidthMeasure();
  },
  { flush: 'post', immediate: true },
);

watch(
  () => isCollapse.value,
  (collapsed) => {
    if (!collapsed && manualExpandedAsideWidthPx.value != null) {
      manualExpandedAsideWidthPx.value = clampExpandedAsideWidth(manualExpandedAsideWidthPx.value);
      persistManualAsideWidth(manualExpandedAsideWidthPx.value);
    }
  },
);

watch(
  () => route.path,
  () => {
    if (manualExpandedAsideWidthPx.value == null) {
      scheduleAsideWidthMeasure();
    }
  },
);
</script>

<template>
  <div class="common-layout">
    <el-container>
      <el-aside
        ref="asideRef"
        :width="asideWidth"
        class="main-aside"
        :class="{ 'is-resizing': isResizingAside }"
      >
        <div class="toggle-button" :class="{ 'collapsed': isCollapse }" @click="toggleCollapse">
          <el-icon v-if="isCollapse"><Expand /></el-icon>
          <el-icon v-else><Fold /></el-icon>
        </div>
        <el-menu
          :key="menuRenderKey"
          :default-active="activeMenu"
          :default-openeds="defaultOpeneds"
          class="el-menu-vertical-demo"
          :collapse="isCollapse"
          @click.capture="recordMenuPointerIntent"
          @select="handleMenuSelect"
          @open="handleMenuStructureChange"
          @close="handleMenuStructureChange"
        >
          <el-menu-item v-if="canAccessFeature('home')" :index="HOME_PATH">
            <el-icon><icon-menu /></el-icon>
            <template #title>{{ HOME_TITLE }}</template>
          </el-menu-item>

          <el-sub-menu v-if="toolsMenuVisible" index="tools">
            <template #title>
              <el-icon><Box /></el-icon>
              <span>{{ TOOLS_TITLE }}</span>
            </template>
            <el-menu-item v-if="canAccessMenuPath(PASSWORD_GENERATOR_PATH)" :index="PASSWORD_GENERATOR_PATH">{{ PASSWORD_GENERATOR_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(IMAGE_BROWSER_PATH)" :index="IMAGE_BROWSER_PATH">{{ IMAGE_BROWSER_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(COLOR_TOOLS_PATH)" :index="COLOR_TOOLS_PATH">{{ COLOR_TOOLS_TITLE }}</el-menu-item>
          </el-sub-menu>

          <el-sub-menu v-if="aiToolsMenuVisible" index="ai-tools">
            <template #title>
              <el-icon><ChatDotRound /></el-icon>
              <span>{{ AI_TOOLS_TITLE }}</span>
            </template>
            <el-menu-item v-if="canAccessMenuPath(AI_CONFIG_PATH)" :index="AI_CONFIG_PATH">{{ AI_CONFIG_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(AI_CODEX_SAVER_PATH)" :index="AI_CODEX_SAVER_PATH">{{ AI_CODEX_SAVER_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(AI_EVOMIND_PATH)" :index="AI_EVOMIND_PATH">{{ AI_EVOMIND_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(CLUSTER_CODEX_PATH)" :index="CLUSTER_CODEX_PATH">{{ CLUSTER_CODEX_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(AI_CHAT_PATH)" :index="AI_CHAT_PATH">{{ AI_CHAT_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(AI_REDUCTION_PATH)" :index="AI_REDUCTION_PATH">{{ AI_REDUCTION_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(AI_GIT_COMMIT_PATH)" :index="AI_GIT_COMMIT_PATH">{{ AI_GIT_COMMIT_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(AI_NOTEBOOK_PATH)" :index="AI_NOTEBOOK_PATH">{{ AI_NOTEBOOK_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(AI_WECHAT_PATH)" :index="AI_WECHAT_PATH">{{ AI_WECHAT_TITLE }}</el-menu-item>
          </el-sub-menu>

          <el-sub-menu v-if="attendanceMenuVisible" index="attendance-tools">
            <template #title>
              <el-icon><Calendar /></el-icon>
              <span>{{ ATTENDANCE_TOOLS_TITLE }}</span>
            </template>
            <el-menu-item v-if="canAccessMenuPath(ATTENDANCE_CONFIGS_PATH)" :index="ATTENDANCE_CONFIGS_PATH">{{ ATTENDANCE_CONFIGS_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(ATTENDANCE_HEADER_TOOL_PATH)" :index="ATTENDANCE_HEADER_TOOL_PATH">{{ ATTENDANCE_HEADER_TOOL_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(ATTENDANCE_WJX_COLLECT_PATH)" :index="ATTENDANCE_WJX_COLLECT_PATH">{{ ATTENDANCE_WJX_COLLECT_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(ATTENDANCE_ORDERS_PATH)" :index="ATTENDANCE_ORDERS_PATH">{{ ATTENDANCE_ORDERS_TITLE }}</el-menu-item>
          </el-sub-menu>
          
          <el-sub-menu v-if="gameToolsMenuVisible" index="game-tools">
            <template #title>
              <el-icon><MagicStick /></el-icon>
              <span>{{ GAME_TOOLS_TITLE }}</span>
            </template>
            <el-menu-item v-if="canAccessMenuPath(DSP_CALCULATOR_PATH)" :index="DSP_CALCULATOR_PATH">
              <span>{{ DSP_CALCULATOR_TITLE }}</span>
            </el-menu-item>
            <el-sub-menu v-if="magicCraftMenuVisible" index="magic-craft">
              <template #title>
                <span>{{ MAGIC_CRAFT_TITLE }}</span>
              </template>
              <el-menu-item v-if="canAccessMenuPath(MAGIC_CRAFT_XOR_MATRIX_PATH)" :index="MAGIC_CRAFT_XOR_MATRIX_PATH">{{ MAGIC_CRAFT_XOR_MATRIX_TITLE }}</el-menu-item>
            </el-sub-menu>
            <el-sub-menu v-if="fanxiuMenuVisible" index="fanxiu">
              <template #title>
                <span>{{ FANXIU_TITLE }}</span>
              </template>
              <el-menu-item v-if="canAccessMenuPath(FANXIU_TASK_STATUS_PATH)" :index="FANXIU_TASK_STATUS_PATH">{{ FANXIU_TASK_STATUS_TITLE }}</el-menu-item>
              <el-sub-menu v-if="fanxiuActivityListMenuVisible" :index="FANXIU_ACTIVITY_LIST_SUBMENU_INDEX">
                <template #title>
                  <span class="menu-submenu-route-title" @click.stop="handleMenuTitleNavigate(fanxiuActivityListMenuEntryPath, $event)">
                    {{ FANXIU_ACTIVITY_LIST_TITLE }}
                  </span>
                </template>
                <el-menu-item
                  v-if="canAccessMenuPath(FANXIU_ACTIVITY_LIST_KUNLUN_SECRET_PATH)"
                  :index="FANXIU_ACTIVITY_LIST_KUNLUN_SECRET_PATH"
                >
                  {{ FANXIU_ACTIVITY_LIST_KUNLUN_SECRET_TITLE }}
                </el-menu-item>
                <el-menu-item
                  v-if="canAccessMenuPath(FANXIU_ACTIVITY_LIST_MODAO_INVASION_PATH)"
                  :index="FANXIU_ACTIVITY_LIST_MODAO_INVASION_PATH"
                >
                  {{ FANXIU_ACTIVITY_LIST_MODAO_INVASION_TITLE }}
                </el-menu-item>
                <el-menu-item
                  v-if="canAccessMenuPath(FANXIU_ACTIVITY_LIST_SHOUYUAN_EXPLORATION_PATH)"
                  :index="FANXIU_ACTIVITY_LIST_SHOUYUAN_EXPLORATION_PATH"
                >
                  {{ FANXIU_ACTIVITY_LIST_SHOUYUAN_EXPLORATION_TITLE }}
                </el-menu-item>
                <el-menu-item
                  v-if="canAccessMenuPath(FANXIU_ACTIVITY_LIST_DIVINE_RESOURCE_PATH)"
                  :index="FANXIU_ACTIVITY_LIST_DIVINE_RESOURCE_PATH"
                >
                  {{ FANXIU_ACTIVITY_LIST_DIVINE_RESOURCE_TITLE }}
                </el-menu-item>
                <el-menu-item
                  v-if="canAccessMenuPath(FANXIU_ACTIVITY_LIST_XIANZHOU_MARATHON_PATH)"
                  :index="FANXIU_ACTIVITY_LIST_XIANZHOU_MARATHON_PATH"
                >
                  {{ FANXIU_ACTIVITY_LIST_XIANZHOU_MARATHON_TITLE }}
                </el-menu-item>
              </el-sub-menu>
              <el-menu-item v-if="canAccessMenuPath(FANXIU_REGION_DATA_PATH)" :index="FANXIU_REGION_DATA_PATH">{{ FANXIU_REGION_DATA_TITLE }}</el-menu-item>
              <el-sub-menu v-if="fanxiuInventoryMenuVisible" index="fanxiu-inventory">
                <template #title>
                  <span>{{ FANXIU_INVENTORY_TITLE }}</span>
                </template>
                <el-menu-item v-if="canAccessMenuPath(FANXIU_WARDROBE_HALL_PATH)" :index="FANXIU_WARDROBE_HALL_PATH">{{ FANXIU_WARDROBE_HALL_TITLE }}</el-menu-item>
                <el-menu-item v-if="canAccessMenuPath(FANXIU_SPIRIT_BEAST_HALL_PATH)" :index="FANXIU_SPIRIT_BEAST_HALL_PATH">{{ FANXIU_SPIRIT_BEAST_HALL_TITLE }}</el-menu-item>
                <el-sub-menu v-if="fanxiuMagicTreasureMenuVisible" :index="FANXIU_MAGIC_TREASURE_SUBMENU_INDEX">
                  <template #title>
                    <span class="menu-submenu-route-title" @click.stop="handleMenuTitleNavigate(fanxiuMagicTreasureMenuEntryPath, $event)">
                      {{ FANXIU_MAGIC_TREASURE_HALL_TITLE }}
                    </span>
                  </template>
                  <el-menu-item
                    v-if="canAccessMenuPath(FANXIU_MAGIC_TREASURE_FORMATIONS_PATH)"
                    :index="FANXIU_MAGIC_TREASURE_FORMATIONS_PATH"
                  >
                    {{ FANXIU_MAGIC_TREASURE_FORMATIONS_TITLE }}
                  </el-menu-item>
                </el-sub-menu>
              </el-sub-menu>
              <el-menu-item v-if="canAccessMenuPath(FANXIU_LABELME_PATH)" :index="FANXIU_LABELME_PATH">{{ FANXIU_LABELME_TITLE }}</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath(FANXIU_CALCULATOR_PATH)" :index="FANXIU_CALCULATOR_PATH">{{ FANXIU_CALCULATOR_TITLE }}</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath(FANXIU_DRAW_CALC_PATH)" :index="FANXIU_DRAW_CALC_PATH">{{ FANXIU_DRAW_CALC_TITLE }}</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath(FANXIU_LOTTERY_MODEL_PATH)" :index="FANXIU_LOTTERY_MODEL_PATH">{{ FANXIU_LOTTERY_MODEL_TITLE }}</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath(FANXIU_DISCOUNT_PATH)" :index="FANXIU_DISCOUNT_PATH">{{ FANXIU_DISCOUNT_TITLE }}</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath(FANXIU_RECHARGE_PATH)" :index="FANXIU_RECHARGE_PATH">{{ FANXIU_RECHARGE_TITLE }}</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath(FANXIU_CUIJIAN_TRIAL_PATH)" :index="FANXIU_CUIJIAN_TRIAL_PATH">{{ FANXIU_CUIJIAN_TRIAL_TITLE }}</el-menu-item>
            </el-sub-menu>
          </el-sub-menu>

          <el-sub-menu v-if="noteToolsMenuVisible" index="note-tools">
            <template #title>
              <el-icon><Document /></el-icon>
              <span>{{ NOTE_TOOLS_TITLE }}</span>
            </template>
            <el-menu-item v-if="canAccessMenuPath(NOTES_CENTER_MENU_PATH)" :index="NOTES_CENTER_MENU_PATH">{{ NOTES_CENTER_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(NOTES_SHEETS_MANAGER_PATH)" :index="NOTES_SHEETS_MANAGER_PATH">{{ NOTES_SHEETS_MANAGER_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(EASTMONEY_PATH)" :index="EASTMONEY_PATH">{{ EASTMONEY_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(FREEBILL_PATH)" :index="FREEBILL_PATH">{{ FREEBILL_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(NOTES_WECHAT_PATH)" :index="NOTES_WECHAT_PATH">{{ NOTES_WECHAT_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(NOTES_INFINITE_CANVAS_PATH)" :index="NOTES_INFINITE_CANVAS_PATH">{{ NOTES_INFINITE_CANVAS_TITLE }}</el-menu-item>
          </el-sub-menu>

          <el-sub-menu
            v-for="section in visibleStandalonePluginMenuSections"
            :key="section.key"
            :index="section.key"
          >
            <template #title>
              <el-icon><Box /></el-icon>
              <span>{{ section.title }}</span>
            </template>
            <el-menu-item
              v-for="item in section.items"
              :key="item.key"
              :index="item.path"
            >
              {{ item.title }}
            </el-menu-item>
          </el-sub-menu>

          <el-sub-menu v-if="clusterMenuVisible" index="cluster-tools">
            <template #title>
              <el-icon><Monitor /></el-icon>
              <span>{{ CLUSTER_TOOLS_TITLE }}</span>
            </template>
            <el-menu-item v-if="canAccessMenuPath(CLUSTER_TASKS_PATH)" :index="CLUSTER_TASKS_PATH">{{ CLUSTER_TASKS_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(CLUSTER_STORAGE_PATH)" :index="CLUSTER_STORAGE_PATH">{{ CLUSTER_STORAGE_TITLE }}</el-menu-item>
            <el-sub-menu v-if="clusterFilesMenuVisible" :index="CLUSTER_FILES_SUBMENU_INDEX">
              <template #title>
                <span class="menu-submenu-route-title" @click.stop="handleMenuTitleNavigate(clusterFilesMenuEntryPath, $event)">
                  {{ CLUSTER_FILES_TITLE }}
                </span>
              </template>
              <el-menu-item v-if="canAccessMenuPath(CLUSTER_VIEW_MN_PATH)" :index="CLUSTER_VIEW_MN_PATH">{{ CLUSTER_VIEW_MN_TITLE }}</el-menu-item>
            </el-sub-menu>
            <el-menu-item
              v-for="item in clusterPluginMenuItems"
              :key="item.key"
              :index="item.path"
            >
              {{ item.title }}
            </el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(CLUSTER_LABELME_PATH)" :index="CLUSTER_LABELME_PATH">{{ CLUSTER_LABELME_TITLE }}</el-menu-item>
          </el-sub-menu>

          <el-sub-menu v-if="adminMenuVisible" index="admin-tools">
            <template #title>
              <el-icon><Setting /></el-icon>
              <span>{{ ADMIN_TOOLS_TITLE }}</span>
            </template>
            <el-menu-item v-if="canAccessMenuPath(ADMIN_ACCOUNTS_PATH)" :index="ADMIN_ACCOUNTS_PATH">
              <span>{{ ADMIN_ACCOUNTS_TITLE }}</span>
            </el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(ADMIN_IMAGES_PATH)" :index="ADMIN_IMAGES_PATH">
              <span>{{ ADMIN_IMAGES_TITLE }}</span>
            </el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(ADMIN_BACKGROUND_TASKS_PATH)" :index="ADMIN_BACKGROUND_TASKS_PATH">
              <span>{{ ADMIN_BACKGROUND_TASKS_TITLE }}</span>
            </el-menu-item>
          </el-sub-menu>
        </el-menu>
        
        <div class="aside-disclaimer" :class="{ 'collapsed': isCollapse }">
          <el-tooltip
            effect="dark"
            content="个人实验项目：不对数据隐私及备份安全负责，请勿存储敏感信息并定期备份数据。"
            placement="right"
            :disabled="!isCollapse"
          >
            <div class="disclaimer-content">
              <el-icon><InfoFilled /></el-icon>
              <span v-if="!isCollapse">免责：实验项目，勿存私密敏感信息，请自行备份。</span>
            </div>
          </el-tooltip>
        </div>
        <div
          v-if="!isCollapse"
          class="aside-resize-handle"
          title="拖拽调整侧边栏宽度，双击恢复自动宽度"
          @mousedown="startAsideResize"
          @dblclick.stop="resetAsideWidth"
        />
      </el-aside>
      <el-container>
        <el-header>
          <div class="header-content">
            <a
              v-if="standaloneRouteTarget"
              :href="standaloneRouteHref"
              target="_blank"
              rel="noopener noreferrer"
              class="header-link-button"
            >
              <el-button
                size="small"
                plain
              >
                单独打开本页
              </el-button>
            </a>
          </div>
          <div class="header-actions">
            <template v-if="userStore.isAuthenticated">
              <span class="username">
                <el-icon><User /></el-icon>
                {{ userStore.user?.username || '用户' }}
              </span>
              <el-button type="danger" link @click="handleLogout">
                <el-icon><SwitchButton /></el-icon>
                退出
              </el-button>
            </template>
            <template v-else>
              <el-button type="primary" link @click="handleLogin">
                登录
              </el-button>
            </template>
          </div>
        </el-header>
        <el-main class="page-shell-main">
          <router-view />
        </el-main>
      </el-container>
    </el-container>
  </div>
</template>

<style scoped>
.common-layout {
  height: 100dvh;
  min-height: 100dvh;
  display: flex;
  width: 100%; /* Ensure full width */
  overflow: hidden;
}
.el-container {
  height: 100%;
  width: 100%;
  min-height: 0;
  min-width: 0;
}
.el-aside {
  background-color: #f5f7fa;
  border-right: 1px solid #e6e6e6;
  transition: width 0.3s;
  overflow-x: hidden;
  display: flex;
  flex-direction: column;
}

.main-aside {
  min-height: 0;
  position: relative;
}

.main-aside.is-resizing {
  transition: none;
}

.toggle-button {
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding-right: 20px;
  cursor: pointer;
  font-size: 20px;
  color: #606266;
  border-bottom: 1px solid #e6e6e6;
}

.toggle-button.collapsed {
  justify-content: center;
  padding-right: 0;
}

.el-header {
  background-color: #fff;
  border-bottom: 1px solid #e6e6e6;
  display: flex;
  align-items: center;
  justify-content: space-between; /* Space out title and actions */
  padding: 0 20px; /* Adjust padding */
  flex-shrink: 0;
}
.header-content {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.header-link-button {
  display: inline-flex;
  text-decoration: none;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 15px;
}
.username {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 14px;
  color: #606266;
}
.el-main {
  padding: 0; /* Remove default padding to allow children to control layout */
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: auto;
}
.el-menu {
  border-right: none;
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
}

:deep(.el-menu-vertical-demo:not(.el-menu--collapse) .el-menu-item),
:deep(.el-menu-vertical-demo:not(.el-menu--collapse) .el-sub-menu__title) {
  height: auto;
  min-height: 42px;
  padding-top: 10px;
  padding-bottom: 10px;
  box-sizing: border-box;
  line-height: 1.35;
  white-space: normal;
}

:deep(.el-menu-vertical-demo:not(.el-menu--collapse) .el-menu-item > span),
:deep(.el-menu-vertical-demo:not(.el-menu--collapse) .el-sub-menu__title > span) {
  min-width: 0;
  white-space: normal;
  overflow-wrap: anywhere;
  word-break: break-word;
  line-height: 1.35;
}

.menu-submenu-route-title {
  display: block;
  min-width: 0;
  width: 100%;
  cursor: pointer;
  white-space: normal;
  overflow-wrap: anywhere;
  word-break: break-word;
  line-height: 1.35;
}

.menu-submenu-route-title:hover {
  color: var(--el-menu-active-color);
}

.aside-disclaimer {
  padding: 15px;
  border-top: 1px solid #e6e6e6;
  background-color: #f9fafc;
  font-size: 11px;
  color: #909399;
  line-height: 1.4;
}

.aside-disclaimer.collapsed {
  padding: 10px;
  display: flex;
  justify-content: center;
}

.disclaimer-content {
  display: flex;
  align-items: flex-start;
  gap: 6px;
}

.disclaimer-content .el-icon {
  font-size: 14px;
  flex-shrink: 0;
  margin-top: 2px;
}

.aside-resize-handle {
  position: absolute;
  top: 0;
  right: 0;
  width: 10px;
  height: 100%;
  cursor: col-resize;
  z-index: 4;
}

.aside-resize-handle::before {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  width: 2px;
  height: 100%;
  background: transparent;
  transition: background-color 0.18s ease;
}

.aside-resize-handle:hover::before,
.main-aside.is-resizing .aside-resize-handle::before {
  background: rgba(64, 158, 255, 0.7);
}
</style>
