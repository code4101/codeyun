<script setup lang="ts">
import { ref, computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { findPermissionKeyByMenuPath } from '@/features/access/permissionRegistry';
import {
  findPrivateMenuIndex,
  getDefaultPrivateOpeneds,
  isPrivateMenuItemVisible,
  privateMenuSections,
} from '@/private';
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

const toggleCollapse = () => {
  isCollapse.value = !isCollapse.value;
};

const activeMenu = computed(() => {
  if (route.path === '/cluster') return '/cluster/tasks';
  if (route.path.startsWith('/cluster/files') || route.path.startsWith('/cluster/media') || route.path.startsWith('/cluster/images')) {
    return '/cluster/files';
  }
  if (route.path.startsWith('/cluster/labelme')) {
    return '/cluster/labelme';
  }
  if (route.path.startsWith('/attendance/questionnaire/feedback') || route.path.startsWith('/attendance/wjx/feedback')) {
    return '/attendance/questionnaire/feedback';
  }
  if (route.path.startsWith('/attendance/questionnaire/data') || route.path.startsWith('/attendance/wjx/data')) {
    return '/attendance/questionnaire/data';
  }
  if (
    route.path.startsWith('/attendance/questionnaire')
    || route.path.startsWith('/attendance/wjx')
  ) {
    return '/attendance/questionnaire/feedback';
  }
  if (route.path.startsWith('/cluster/')) return '/cluster/tasks';
  const privateMenuIndex = findPrivateMenuIndex(route.path);
  if (privateMenuIndex) return privateMenuIndex;
  return route.path;
});

const GREEN_CHANNEL_MENU_PATHS = new Set<string>([
  '/attendance/questionnaire/feedback',
])

const canAccessFeature = (key: string) => featureAccessStore.isAllowed(key);

const canAccessMenuPath = (path: string) => {
  if (GREEN_CHANNEL_MENU_PATHS.has(path)) {
    return true
  }
  const permissionKey = findPermissionKeyByMenuPath(path);
  if (!permissionKey) {
    return false;
  }
  return featureAccessStore.isAllowed(permissionKey);
};

const attendanceFeedbackGreenChannelVisible = computed(() =>
  canAccessMenuPath('/attendance/questionnaire/feedback'),
)

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
    '/tools/password-generator',
    '/tools/image-browser',
    '/tools/color-tools',
  ].some((path) => canAccessMenuPath(path)),
);

const aiToolsMenuVisible = computed(() =>
  canAccessFeature('ai-tools')
  && [
    '/tools/ai-config',
    '/tools/ai-chat',
    '/tools/ai-reduction',
    '/tools/ai-git-commit',
  ].some((path) => canAccessMenuPath(path)),
);

const attendanceMenuVisible = computed(() =>
  attendanceFeedbackGreenChannelVisible.value
  || (
    canAccessFeature('attendance-tools')
    && [
      '/attendance/configs',
      '/attendance/questionnaire/feedback',
      '/attendance/questionnaire/data',
      '/attendance/orders',
    ].some((path) => canAccessMenuPath(path))
  ),
);

const attendanceWjxMenuVisible = computed(() =>
  attendanceFeedbackGreenChannelVisible.value
  || (
    canAccessFeature('attendance.wjx')
    && ['/attendance/questionnaire/feedback', '/attendance/questionnaire/data'].some((path) => canAccessMenuPath(path))
  ),
);

const fanxiuMenuVisible = computed(() =>
  canAccessFeature('fanxiu')
  && [
    '/fanxiu/calculator',
    '/fanxiu/draw-calc',
    '/fanxiu/discount',
    '/fanxiu/task-status',
    '/fanxiu/recharge',
    '/fanxiu/xianzhou-race',
    '/fanxiu/cuijian-trial',
  ].some((path) => canAccessMenuPath(path)),
);

const magicCraftMenuVisible = computed(() =>
  canAccessFeature('magic-craft')
  && canAccessMenuPath('/magic-craft/xor-matrix'),
);

const gameToolsMenuVisible = computed(() =>
  canAccessFeature('game-tools')
  && (
    fanxiuMenuVisible.value
    || magicCraftMenuVisible.value
    || canAccessMenuPath('/dsp/calculator')
  ),
);

const noteToolsMenuVisible = computed(() =>
  canAccessFeature('note-tools')
  && [
    '/notes/star-map',
    '/notes/infinite-canvas',
  ].some((path) => canAccessMenuPath(path)),
);

const clusterMenuVisible = computed(() =>
  canAccessFeature('cluster-tools')
  && [
    '/cluster/tasks',
    '/cluster/files',
    '/cluster/labelme',
  ].some((path) => canAccessMenuPath(path)),
);

const adminMenuVisible = computed(() =>
  userStore.isAdmin
  && canAccessFeature('admin-tools')
  && [
    '/admin/accounts',
    '/admin/images',
  ].some((path) => canAccessMenuPath(path)),
);

const defaultOpeneds = computed(() => {
  const openeds: string[] = [];
  if (route.path === '/cluster') return ['cluster-tools'];
  if (route.path.startsWith('/cluster/')) openeds.push('cluster-tools');
  if (route.path.startsWith('/admin/')) openeds.push('admin-tools');
  if (route.path.startsWith('/tools/ai-')) openeds.push('ai-tools');
  if (route.path.startsWith('/attendance/')) openeds.push('attendance-tools');
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
          <el-menu-item v-if="canAccessFeature('home')" index="/">
            <el-icon><icon-menu /></el-icon>
            <template #title>首页</template>
          </el-menu-item>

          <el-sub-menu v-if="toolsMenuVisible" index="tools">
            <template #title>
              <el-icon><Box /></el-icon>
              <span>综合工具</span>
            </template>
            <el-menu-item v-if="canAccessMenuPath('/tools/password-generator')" index="/tools/password-generator">随机密码</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath('/tools/image-browser')" index="/tools/image-browser">文件浏览</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath('/tools/color-tools')" index="/tools/color-tools">颜色工具</el-menu-item>
          </el-sub-menu>

          <el-sub-menu v-if="aiToolsMenuVisible" index="ai-tools">
            <template #title>
              <el-icon><ChatDotRound /></el-icon>
              <span>AI工具</span>
            </template>
            <el-menu-item v-if="canAccessMenuPath('/tools/ai-config')" index="/tools/ai-config">配置</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath('/tools/ai-chat')" index="/tools/ai-chat">AI聊天</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath('/tools/ai-reduction')" index="/tools/ai-reduction">AI归纳</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath('/tools/ai-git-commit')" index="/tools/ai-git-commit">AI提交</el-menu-item>
          </el-sub-menu>

          <el-sub-menu v-if="attendanceMenuVisible" index="attendance-tools">
            <template #title>
              <el-icon><Document /></el-icon>
              <span>禅寺考勤</span>
            </template>
            <el-menu-item v-if="canAccessMenuPath('/attendance/configs')" index="/attendance/configs">考勤配置</el-menu-item>
            <el-sub-menu v-if="attendanceWjxMenuVisible" index="attendance-questionnaire">
              <template #title>
                <span>问卷</span>
              </template>
              <el-menu-item v-if="canAccessMenuPath('/attendance/questionnaire/feedback')" index="/attendance/questionnaire/feedback">配置</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath('/attendance/questionnaire/data')" index="/attendance/questionnaire/data">数据</el-menu-item>
            </el-sub-menu>
            <el-menu-item v-if="canAccessMenuPath('/attendance/orders')" index="/attendance/orders">订单操作</el-menu-item>
          </el-sub-menu>
          
          <el-sub-menu v-if="gameToolsMenuVisible" index="game-tools">
            <template #title>
              <el-icon><MagicStick /></el-icon>
              <span>游戏工具</span>
            </template>
            <el-menu-item v-if="canAccessMenuPath('/dsp/calculator')" index="/dsp/calculator">
              <span>戴森球计划</span>
            </el-menu-item>
            <el-sub-menu v-if="magicCraftMenuVisible" index="magic-craft">
              <template #title>
                <span>魔法工艺</span>
              </template>
              <el-menu-item v-if="canAccessMenuPath('/magic-craft/xor-matrix')" index="/magic-craft/xor-matrix">点灯解谜</el-menu-item>
            </el-sub-menu>
            <el-sub-menu v-if="fanxiuMenuVisible" index="fanxiu">
              <template #title>
                <span>凡修手游</span>
              </template>
              <el-menu-item v-if="canAccessMenuPath('/fanxiu/calculator')" index="/fanxiu/calculator">兽魂计算器</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath('/fanxiu/draw-calc')" index="/fanxiu/draw-calc">活动抽数计算</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath('/fanxiu/discount')" index="/fanxiu/discount">凡修优惠券</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath('/fanxiu/task-status')" index="/fanxiu/task-status">任务状态</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath('/fanxiu/recharge')" index="/fanxiu/recharge">充值礼包(Beta)</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath('/fanxiu/xianzhou-race')" index="/fanxiu/xianzhou-race">仙舟竞速</el-menu-item>
              <el-menu-item v-if="canAccessMenuPath('/fanxiu/cuijian-trial')" index="/fanxiu/cuijian-trial">淬剑试炼</el-menu-item>
            </el-sub-menu>
          </el-sub-menu>

          <el-sub-menu v-if="noteToolsMenuVisible" index="note-tools">
            <template #title>
              <el-icon><Document /></el-icon>
              <span>笔记工具</span>
            </template>
            <el-menu-item v-if="canAccessMenuPath('/notes/star-map')" index="/notes/star-map">星图笔记</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath('/notes/infinite-canvas')" index="/notes/infinite-canvas">无限画布</el-menu-item>
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
              <span>集群管理</span>
            </template>
            <el-menu-item v-if="canAccessMenuPath('/cluster/tasks')" index="/cluster/tasks">设备任务</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath('/cluster/files')" index="/cluster/files">浏览文件</el-menu-item>
            <el-menu-item v-if="canAccessMenuPath('/cluster/labelme')" index="/cluster/labelme">图片标注</el-menu-item>
          </el-sub-menu>

          <el-sub-menu v-if="adminMenuVisible" index="admin-tools">
            <template #title>
              <el-icon><Setting /></el-icon>
              <span>系统管理</span>
            </template>
            <el-menu-item v-if="canAccessMenuPath('/admin/accounts')" index="/admin/accounts">
              <span>账号管理</span>
            </el-menu-item>
            <el-menu-item v-if="canAccessMenuPath('/admin/images')" index="/admin/images">
              <span>存储维护</span>
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
            <!-- <h2>CodeYun</h2> -->
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
