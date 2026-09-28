<script setup lang="ts">
import { ref, computed, nextTick, onBeforeUnmount, onMounted, watch } from 'vue';
import type { ComponentPublicInstance } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  buildVisibleAppDirectory,
  findDirectoryNodeByMenuPath,
  getDirectoryOpenKeys,
  isDirectorySubmenu,
} from '@/features/access/appDirectory';
import AppDirectoryMenuNode from '@/components/layout/AppDirectoryMenuNode.vue';
import {
  getMatchedMenuPath,
} from '@/router/pageRegistry';
import { useFeatureAccessStore } from '@/store/featureAccessStore';
import { useUserStore } from '@/store/userStore';
import { ArrowRight, Expand, Fold, SwitchButton, User } from '@element-plus/icons-vue';

const route = useRoute();
const router = useRouter();
const featureAccessStore = useFeatureAccessStore();
const userStore = useUserStore();
const isCollapse = ref(false);
const isCompactViewport = ref(false);
const collapseForcedByCompactViewport = ref(false);
const isResizingAside = ref(false);
const asideRef = ref<HTMLElement | ComponentPublicInstance | null>(null);
const COLLAPSED_ASIDE_WIDTH = 64;
const MIN_EXPANDED_ASIDE_WIDTH = 200;
const MAX_EXPANDED_ASIDE_WIDTH = 420;
const COMPACT_VIEWPORT_MAX_WIDTH = 900;
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

const toggleCollapse = () => {
  collapseForcedByCompactViewport.value = false;
  isCollapse.value = !isCollapse.value;
};

const syncCollapseForViewport = () => {
  const compact = window.innerWidth <= COMPACT_VIEWPORT_MAX_WIDTH;
  if (compact === isCompactViewport.value) {
    return;
  }

  isCompactViewport.value = compact;

  if (compact) {
    if (!isCollapse.value) {
      isCollapse.value = true;
      collapseForcedByCompactViewport.value = true;
    }
    return;
  }

  if (collapseForcedByCompactViewport.value) {
    isCollapse.value = false;
    collapseForcedByCompactViewport.value = false;
  }
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

const visibleDirectory = computed(() => buildVisibleAppDirectory({
  layout: featureAccessStore.flatItems,
  isAllowed: (permissionKey) => featureAccessStore.isAllowed(permissionKey),
  isAuthenticated: userStore.isAuthenticated,
  isAdmin: userStore.isAdmin,
}));

const mainDirectoryNodes = computed(() =>
  visibleDirectory.value.filter((node) => node.slot === 'main'),
);

const footerDirectoryNodes = computed(() =>
  visibleDirectory.value.filter((node) => node.slot === 'footer'),
);

const activeMenu = computed(() => {
  const matchedMenuPath = getMatchedMenuPath(route) ?? route.path;
  const matchedNode = findDirectoryNodeByMenuPath(visibleDirectory.value, matchedMenuPath);
  if (
    matchedNode
    && isDirectorySubmenu(matchedNode)
    && matchedNode.menuItems.some((item) => item.path === matchedMenuPath)
  ) {
    return matchedNode.key;
  }
  return matchedMenuPath;
});

const defaultOpeneds = computed(() => {
  const matchedMenuPath = getMatchedMenuPath(route) ?? route.path;
  return getDirectoryOpenKeys(mainDirectoryNodes.value, matchedMenuPath);
});
const handleLogout = () => {
  userStore.logout();
  router.push('/login');
};

const handleLogin = () => {
  router.push('/login');
};

const buildMenuRouteLocation = (path: string) => {
  const currentEntryId = Array.isArray(route.query.entry_id) ? route.query.entry_id[0] : route.query.entry_id;
  if (!path.startsWith('/cluster/')) {
    return { path };
  }
  return {
    path,
    query: currentEntryId ? { entry_id: currentEntryId } : {},
  };
};

const openMenuPathInNewTab = (path: string) => {
  window.open(router.resolve(buildMenuRouteLocation(path)).href, '_blank', 'noopener,noreferrer');
};

const navigateMenuPath = (path: string) => {
  const target = buildMenuRouteLocation(path);
  if (route.fullPath !== router.resolve(target).fullPath) {
    void router.push(target);
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
  syncCollapseForViewport();
  if (manualExpandedAsideWidthPx.value != null) {
    manualExpandedAsideWidthPx.value = clampExpandedAsideWidth(manualExpandedAsideWidthPx.value);
    persistManualAsideWidth(manualExpandedAsideWidthPx.value);
    return;
  }
  scheduleAsideWidthMeasure();
};

onMounted(() => {
  syncCollapseForViewport();
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
        <div class="aside-toolbar" :class="{ 'collapsed': isCollapse }">
          <el-tooltip :content="isCollapse ? '展开侧栏' : '收起侧栏'" placement="right">
            <button class="toggle-button" type="button" :aria-label="isCollapse ? '展开侧栏' : '收起侧栏'" :aria-expanded="!isCollapse" @click="toggleCollapse">
              <el-icon v-if="isCollapse"><Expand /></el-icon>
              <el-icon v-else><Fold /></el-icon>
            </button>
          </el-tooltip>
        </div>
        <el-menu
          :key="menuRenderKey"
          :default-active="activeMenu"
          :default-openeds="defaultOpeneds"
          class="main-menu el-menu-vertical-demo"
          :collapse="isCollapse"
          @click.capture="recordMenuPointerIntent"
          @select="handleMenuSelect"
          @open="handleMenuStructureChange"
          @close="handleMenuStructureChange"
        >
          <AppDirectoryMenuNode
            v-for="node in mainDirectoryNodes"
            :key="node.key"
            :node="node"
            @title-navigate="handleMenuTitleNavigate"
          />
        </el-menu>

        <el-menu
          v-if="footerDirectoryNodes.length"
          :key="`bottom-${menuRenderKey}`"
          :default-active="activeMenu"
          class="aside-bottom-menu el-menu-vertical-demo"
          :collapse="isCollapse"
          @click.capture="recordMenuPointerIntent"
          @select="handleMenuSelect"
        >
          <AppDirectoryMenuNode
            v-for="node in footerDirectoryNodes"
            :key="node.key"
            :node="node"
            @title-navigate="handleMenuTitleNavigate"
          />
        </el-menu>
        
        <div class="aside-account" :class="{ 'collapsed': isCollapse }">
          <el-dropdown v-if="userStore.isAuthenticated" trigger="click" placement="right-end" @command="handleLogout">
            <button type="button" class="account-button" :aria-label="`账号：${userStore.user?.username || '用户'}`" :title="userStore.user?.username || '用户'">
              <span class="account-avatar"><el-icon><User /></el-icon></span>
              <span v-if="!isCollapse" class="account-name">{{ userStore.user?.username || '用户' }}</span>
              <el-icon v-if="!isCollapse" class="account-chevron"><ArrowRight /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item disabled>{{ userStore.user?.username || '用户' }}</el-dropdown-item>
                <el-dropdown-item command="logout" divided :icon="SwitchButton">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <button v-else type="button" class="account-button" aria-label="登录" title="登录" @click="handleLogin">
            <span class="account-avatar"><el-icon><User /></el-icon></span>
            <span v-if="!isCollapse" class="account-name">登录</span>
          </button>
        </div>
        <div
          v-if="!isCollapse"
          class="aside-resize-handle"
          title="拖拽调整侧边栏宽度，双击恢复自动宽度"
          @mousedown="startAsideResize"
          @dblclick.stop="resetAsideWidth"
        />
      </el-aside>
      <el-main class="page-shell-main">
        <router-view />
      </el-main>
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

.aside-toolbar {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 12px;
  flex-shrink: 0;
  border-bottom: 1px solid #e6e6e6;
}

.aside-toolbar.collapsed {
  flex-direction: column-reverse;
  padding: 4px 0;
}

.toggle-button,
.account-button {
  display: flex;
  align-items: center;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #606266;
  font: inherit;
  cursor: pointer;
  text-decoration: none;
}

.toggle-button {
  width: 32px;
  height: 32px;
  padding: 0;
  margin-left: auto;
  justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
}



.collapsed .toggle-button {
  margin-left: 0;
}


.toggle-button:hover,
.account-button:hover {
  background: #e9eef5;
  color: var(--el-color-primary);
}

.toggle-button:focus-visible,
.account-button:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: -2px;
}

.aside-account {
  padding: 8px 12px;
  border-top: 1px solid #e6e6e6;
  flex-shrink: 0;
}

.aside-account :deep(.el-dropdown) {
  display: flex;
  min-width: 0;
}

.account-button {
  width: 100%;
  min-width: 0;
  gap: 8px;
  padding: 6px 4px;
  font-size: 14px;
}

.account-avatar {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-size: 18px;
}

.account-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
}

.account-chevron {
  flex-shrink: 0;
  color: #909399;
}

.collapsed .account-button {
  justify-content: center;
}

.el-main {
  padding: 0; /* Remove default padding to allow children to control layout */
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow: auto;
}
.main-menu {
  border-right: none;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}

.aside-bottom-menu {
  border-right: none;
  border-top: 1px solid #e6e6e6;
  flex: 0 0 auto;
  overflow: hidden;
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
