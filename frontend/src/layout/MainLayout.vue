<script setup lang="ts">
import { ref, computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  findPermissionKeyByMenuPath,
  requirePermissionTitle,
  requirePermissionTitleByMenuPath,
} from '@/features/access/permissionRegistry';
import {
  findPrivateMenuIndex,
  getDefaultPrivateOpeneds,
  isPrivateMenuItemVisible,
  privateMenuSections,
} from '@/private';
import {
  getMatchedMenuPath,
  requirePageCanonicalPath,
  requirePageMenuPath,
} from '@/router/pageRegistry';
import { buildStandaloneRouteLocation } from '@/router/standalone';
import { useFeatureAccessStore } from '@/store/featureAccessStore';
import { useUserStore } from '@/store/userStore';
import {
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
const expandedAsideWidth = 'clamp(152px, 40vw, 200px)';
const asideWidth = computed(() => (isCollapse.value ? '64px' : expandedAsideWidth));
const HOME_PATH = requirePageMenuPath('Home');
const PASSWORD_GENERATOR_PATH = requirePageMenuPath('PasswordGenerator');
const IMAGE_BROWSER_PATH = requirePageMenuPath('ImageBrowser');
const COLOR_TOOLS_PATH = requirePageMenuPath('ColorTools');
const AI_CONFIG_PATH = requirePageMenuPath('AiConfig');
const AI_CHAT_PATH = requirePageMenuPath('AiChat');
const AI_REDUCTION_PATH = requirePageMenuPath('AiReduction');
const AI_GIT_COMMIT_PATH = requirePageMenuPath('AiGitCommit');
const ATTENDANCE_CONFIGS_PATH = requirePageMenuPath('AttendanceConfigs');
const ATTENDANCE_WJX_CATALOG_PATH = requirePageMenuPath('AttendanceWjxCatalog');
const ATTENDANCE_WJX_COLLECT_PATH = requirePageMenuPath('AttendanceWjxCollect');
const ATTENDANCE_WJX_DATA_PATH = requirePageMenuPath('AttendanceWjxData');
const ATTENDANCE_ORDERS_PATH = requirePageMenuPath('AttendanceOrders');
const DSP_CALCULATOR_PATH = requirePageMenuPath('DspCalculator');
const MAGIC_CRAFT_XOR_MATRIX_PATH = requirePageMenuPath('XorMatrix');
const FANXIU_CALCULATOR_PATH = requirePageMenuPath('BeastSoulCalculator');
const FANXIU_DRAW_CALC_PATH = requirePageMenuPath('DrawCalculator');
const FANXIU_DISCOUNT_PATH = requirePageMenuPath('FanxiuDiscountGuide');
const FANXIU_TASK_STATUS_PATH = requirePageMenuPath('FanxiuTaskStatus');
const FANXIU_RECHARGE_PATH = requirePageMenuPath('FanxiuRecharge');
const FANXIU_XIANZHOU_RACE_PATH = requirePageMenuPath('XianzhouRace');
const FANXIU_CUIJIAN_TRIAL_PATH = requirePageMenuPath('CuijianTrial');
const NOTES_CENTER_MENU_PATH = requirePageMenuPath('NotesCenter');
const NOTES_INFINITE_CANVAS_PATH = requirePageMenuPath('InfiniteCanvas');
const CLUSTER_TASKS_PATH = requirePageMenuPath('DeviceTasks');
const CLUSTER_FILES_PATH = requirePageMenuPath('DeviceFileBrowser');
const CLUSTER_LABELME_PATH = requirePageMenuPath('DeviceLabelmeBrowser');
const ADMIN_ACCOUNTS_PATH = requirePageMenuPath('AccountManager');
const ADMIN_IMAGES_PATH = requirePageMenuPath('StorageManager');
const ATTENDANCE_PATH_PREFIX = requirePageCanonicalPath('AttendanceConfigs').split('/configs')[0];
const HOME_TITLE = requirePermissionTitle('home');
const TOOLS_TITLE = requirePermissionTitle('tools');
const PASSWORD_GENERATOR_TITLE = requirePermissionTitleByMenuPath(PASSWORD_GENERATOR_PATH);
const IMAGE_BROWSER_TITLE = requirePermissionTitleByMenuPath(IMAGE_BROWSER_PATH);
const COLOR_TOOLS_TITLE = requirePermissionTitleByMenuPath(COLOR_TOOLS_PATH);
const AI_TOOLS_TITLE = requirePermissionTitle('ai-tools');
const AI_CONFIG_TITLE = requirePermissionTitleByMenuPath(AI_CONFIG_PATH);
const AI_CHAT_TITLE = requirePermissionTitleByMenuPath(AI_CHAT_PATH);
const AI_REDUCTION_TITLE = requirePermissionTitleByMenuPath(AI_REDUCTION_PATH);
const AI_GIT_COMMIT_TITLE = requirePermissionTitleByMenuPath(AI_GIT_COMMIT_PATH);
const ATTENDANCE_TOOLS_TITLE = requirePermissionTitle('attendance-tools');
const ATTENDANCE_CONFIGS_TITLE = requirePermissionTitleByMenuPath(ATTENDANCE_CONFIGS_PATH);
const ATTENDANCE_WJX_TITLE = requirePermissionTitle('attendance.wjx');
const ATTENDANCE_WJX_CATALOG_TITLE = requirePermissionTitleByMenuPath(ATTENDANCE_WJX_CATALOG_PATH);
const ATTENDANCE_WJX_COLLECT_TITLE = requirePermissionTitleByMenuPath(ATTENDANCE_WJX_COLLECT_PATH);
const ATTENDANCE_WJX_DATA_TITLE = requirePermissionTitleByMenuPath(ATTENDANCE_WJX_DATA_PATH);
const ATTENDANCE_ORDERS_TITLE = requirePermissionTitleByMenuPath(ATTENDANCE_ORDERS_PATH);
const GAME_TOOLS_TITLE = requirePermissionTitle('game-tools');
const DSP_CALCULATOR_TITLE = requirePermissionTitleByMenuPath(DSP_CALCULATOR_PATH);
const MAGIC_CRAFT_TITLE = requirePermissionTitle('magic-craft');
const MAGIC_CRAFT_XOR_MATRIX_TITLE = requirePermissionTitleByMenuPath(MAGIC_CRAFT_XOR_MATRIX_PATH);
const FANXIU_TITLE = requirePermissionTitle('fanxiu');
const FANXIU_CALCULATOR_TITLE = requirePermissionTitleByMenuPath(FANXIU_CALCULATOR_PATH);
const FANXIU_DRAW_CALC_TITLE = requirePermissionTitleByMenuPath(FANXIU_DRAW_CALC_PATH);
const FANXIU_DISCOUNT_TITLE = requirePermissionTitleByMenuPath(FANXIU_DISCOUNT_PATH);
const FANXIU_TASK_STATUS_TITLE = requirePermissionTitleByMenuPath(FANXIU_TASK_STATUS_PATH);
const FANXIU_RECHARGE_TITLE = requirePermissionTitleByMenuPath(FANXIU_RECHARGE_PATH);
const FANXIU_XIANZHOU_RACE_TITLE = requirePermissionTitleByMenuPath(FANXIU_XIANZHOU_RACE_PATH);
const FANXIU_CUIJIAN_TRIAL_TITLE = requirePermissionTitleByMenuPath(FANXIU_CUIJIAN_TRIAL_PATH);
const NOTE_TOOLS_TITLE = requirePermissionTitle('note-tools');
const NOTES_CENTER_TITLE = requirePermissionTitleByMenuPath(NOTES_CENTER_MENU_PATH);
const NOTES_INFINITE_CANVAS_TITLE = requirePermissionTitleByMenuPath(NOTES_INFINITE_CANVAS_PATH);
const CLUSTER_TOOLS_TITLE = requirePermissionTitle('cluster-tools');
const CLUSTER_TASKS_TITLE = requirePermissionTitleByMenuPath(CLUSTER_TASKS_PATH);
const CLUSTER_FILES_TITLE = requirePermissionTitleByMenuPath(CLUSTER_FILES_PATH);
const CLUSTER_LABELME_TITLE = requirePermissionTitleByMenuPath(CLUSTER_LABELME_PATH);
const ADMIN_TOOLS_TITLE = requirePermissionTitle('admin-tools');
const ADMIN_ACCOUNTS_TITLE = requirePermissionTitleByMenuPath(ADMIN_ACCOUNTS_PATH);
const ADMIN_IMAGES_TITLE = requirePermissionTitleByMenuPath(ADMIN_IMAGES_PATH);

const toggleCollapse = () => {
  isCollapse.value = !isCollapse.value;
};

const activeMenu = computed(() => {
  const matchedMenuPath = getMatchedMenuPath(route);
  if (matchedMenuPath) return matchedMenuPath;
  const privateMenuIndex = findPrivateMenuIndex(route.path);
  if (privateMenuIndex) return privateMenuIndex;
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

const visiblePrivateMenuSections = computed(() =>
  privateMenuSections
    .map((section) => ({
      ...section,
      items: section.items.filter((item) =>
        isPrivateMenuItemVisible(item, userStore.isAuthenticated, userStore.isAdmin)
        && canAccessMenuPath(item.path),
      ),
    }))
    .filter((section) => canAccessFeature(section.key) && section.items.length > 0),
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
    AI_CHAT_PATH,
    AI_REDUCTION_PATH,
    AI_GIT_COMMIT_PATH,
  ].some((path) => canAccessMenuPath(path)),
);

const attendanceMenuVisible = computed(() =>
  canAccessFeature('attendance-tools')
  && [
    ATTENDANCE_CONFIGS_PATH,
    ATTENDANCE_WJX_CATALOG_PATH,
    ATTENDANCE_WJX_COLLECT_PATH,
    ATTENDANCE_WJX_DATA_PATH,
    ATTENDANCE_ORDERS_PATH,
  ].some((path) => canAccessMenuPath(path)),
);

const attendanceWjxMenuVisible = computed(() =>
  canAccessFeature('attendance.wjx')
  && [
    ATTENDANCE_WJX_CATALOG_PATH,
    ATTENDANCE_WJX_COLLECT_PATH,
    ATTENDANCE_WJX_DATA_PATH,
  ].some((path) => canAccessMenuPath(path)),
);

const fanxiuMenuVisible = computed(() =>
  canAccessFeature('fanxiu')
  && [
    FANXIU_CALCULATOR_PATH,
    FANXIU_DRAW_CALC_PATH,
    FANXIU_DISCOUNT_PATH,
    FANXIU_TASK_STATUS_PATH,
    FANXIU_RECHARGE_PATH,
    FANXIU_XIANZHOU_RACE_PATH,
    FANXIU_CUIJIAN_TRIAL_PATH,
  ].some((path) => canAccessMenuPath(path)),
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
    NOTES_INFINITE_CANVAS_PATH,
  ].some((path) => canAccessMenuPath(path)),
);

const clusterMenuVisible = computed(() =>
  canAccessFeature('cluster-tools')
  && [
    CLUSTER_TASKS_PATH,
    CLUSTER_FILES_PATH,
    CLUSTER_LABELME_PATH,
  ].some((path) => canAccessMenuPath(path)),
);

const adminMenuVisible = computed(() =>
  userStore.isAdmin
  && canAccessFeature('admin-tools')
  && [
    ADMIN_ACCOUNTS_PATH,
    ADMIN_IMAGES_PATH,
  ].some((path) => canAccessMenuPath(path)),
);

const defaultOpeneds = computed(() => {
  const openeds: string[] = [];
  if (route.path === ATTENDANCE_PATH_PREFIX) return ['attendance-tools'];
  if (route.path === '/cluster') return ['cluster-tools'];
  if (route.path.startsWith('/cluster/')) openeds.push('cluster-tools');
  if (route.path.startsWith('/admin/')) openeds.push('admin-tools');
  if (route.path.startsWith('/tools/ai-')) openeds.push('ai-tools');
  if (route.path.startsWith(ATTENDANCE_PATH_PREFIX)) openeds.push('attendance-tools');
  if (route.path.startsWith('/attendance/questionnaire') || route.path.startsWith('/attendance/wjx')) openeds.push('attendance-questionnaire');
  if (route.path.startsWith('/tools/')) openeds.push('tools');
  if (route.path.startsWith('/fanxiu/')) openeds.push('game-tools', 'fanxiu');
  if (route.path.startsWith('/magic-craft/')) openeds.push('game-tools', 'magic-craft');
  if (route.path.startsWith('/dsp/')) openeds.push('game-tools');
  openeds.push(...getDefaultPrivateOpeneds(route.path));
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
</script>

<template>
  <div class="common-layout">
    <el-container>
      <el-aside :width="asideWidth" class="main-aside">
        <div class="toggle-button" :class="{ 'collapsed': isCollapse }" @click="toggleCollapse">
          <el-icon v-if="isCollapse"><Expand /></el-icon>
          <el-icon v-else><Fold /></el-icon>
        </div>
        <el-menu
          :default-active="activeMenu"
          :default-openeds="defaultOpeneds"
          class="el-menu-vertical-demo"
          :collapse="isCollapse"
          router
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
            <el-menu-item v-if="canAccessMenuPath(AI_CHAT_PATH)" :index="AI_CHAT_PATH">{{ AI_CHAT_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(AI_REDUCTION_PATH)" :index="AI_REDUCTION_PATH">{{ AI_REDUCTION_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(AI_GIT_COMMIT_PATH)" :index="AI_GIT_COMMIT_PATH">{{ AI_GIT_COMMIT_TITLE }}</el-menu-item>
          </el-sub-menu>

          <el-sub-menu v-if="attendanceMenuVisible" index="attendance-tools">
            <template #title>
              <el-icon><Document /></el-icon>
              <span>{{ ATTENDANCE_TOOLS_TITLE }}</span>
            </template>
            <el-menu-item v-if="canAccessMenuPath(ATTENDANCE_CONFIGS_PATH)" :index="ATTENDANCE_CONFIGS_PATH">{{ ATTENDANCE_CONFIGS_TITLE }}</el-menu-item>
            <el-sub-menu v-if="attendanceWjxMenuVisible" index="attendance-questionnaire">
              <template #title>
                <span>{{ ATTENDANCE_WJX_TITLE }}</span>
              </template>
              <el-menu-item v-if="canAccessMenuPath(ATTENDANCE_WJX_CATALOG_PATH)" :index="ATTENDANCE_WJX_CATALOG_PATH">{{ ATTENDANCE_WJX_CATALOG_TITLE }}</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath(ATTENDANCE_WJX_COLLECT_PATH)" :index="ATTENDANCE_WJX_COLLECT_PATH">{{ ATTENDANCE_WJX_COLLECT_TITLE }}</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath(ATTENDANCE_WJX_DATA_PATH)" :index="ATTENDANCE_WJX_DATA_PATH">{{ ATTENDANCE_WJX_DATA_TITLE }}</el-menu-item>
            </el-sub-menu>
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
              <el-menu-item v-if="canAccessMenuPath(FANXIU_CALCULATOR_PATH)" :index="FANXIU_CALCULATOR_PATH">{{ FANXIU_CALCULATOR_TITLE }}</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath(FANXIU_DRAW_CALC_PATH)" :index="FANXIU_DRAW_CALC_PATH">{{ FANXIU_DRAW_CALC_TITLE }}</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath(FANXIU_DISCOUNT_PATH)" :index="FANXIU_DISCOUNT_PATH">{{ FANXIU_DISCOUNT_TITLE }}</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath(FANXIU_TASK_STATUS_PATH)" :index="FANXIU_TASK_STATUS_PATH">{{ FANXIU_TASK_STATUS_TITLE }}</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath(FANXIU_RECHARGE_PATH)" :index="FANXIU_RECHARGE_PATH">{{ FANXIU_RECHARGE_TITLE }}</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath(FANXIU_XIANZHOU_RACE_PATH)" :index="FANXIU_XIANZHOU_RACE_PATH">{{ FANXIU_XIANZHOU_RACE_TITLE }}</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath(FANXIU_CUIJIAN_TRIAL_PATH)" :index="FANXIU_CUIJIAN_TRIAL_PATH">{{ FANXIU_CUIJIAN_TRIAL_TITLE }}</el-menu-item>
            </el-sub-menu>
          </el-sub-menu>

          <el-sub-menu v-if="noteToolsMenuVisible" index="note-tools">
            <template #title>
              <el-icon><Document /></el-icon>
              <span>{{ NOTE_TOOLS_TITLE }}</span>
            </template>
            <el-menu-item v-if="canAccessMenuPath(NOTES_CENTER_MENU_PATH)" :index="NOTES_CENTER_MENU_PATH">{{ NOTES_CENTER_TITLE }}</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath(NOTES_INFINITE_CANVAS_PATH)" :index="NOTES_INFINITE_CANVAS_PATH">{{ NOTES_INFINITE_CANVAS_TITLE }}</el-menu-item>
          </el-sub-menu>

          <el-sub-menu
            v-for="section in visiblePrivateMenuSections"
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
            <el-menu-item v-if="canAccessMenuPath(CLUSTER_FILES_PATH)" :index="CLUSTER_FILES_PATH">{{ CLUSTER_FILES_TITLE }}</el-menu-item>
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
        <el-main>
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
</style>
