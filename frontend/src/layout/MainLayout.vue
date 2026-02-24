<script setup lang="ts">
import { ref, computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useUserStore } from '@/store/userStore';
import {
  Document,
  Menu as IconMenu,
  Location,
  Monitor,
  User,
  SwitchButton,
  Cellphone,
  MagicStick,
  Star,
  Tools,
  Box
} from '@element-plus/icons-vue';

const route = useRoute();
const router = useRouter();
const userStore = useUserStore();
const isCollapse = ref(false);

const toggleCollapse = () => {
  isCollapse.value = !isCollapse.value;
};

const activeMenu = computed(() => {
  if (route.path.startsWith('/cluster/logs/')) return '/cluster';
  return route.path;
});

const defaultOpeneds = computed(() => {
  if (route.path.startsWith('/fanxiu/')) return ['game-tools', 'fanxiu'];
  if (route.path.startsWith('/magic-craft/')) return ['game-tools', 'magic-craft'];
  if (route.path.startsWith('/dsp/')) return ['game-tools'];
  return [];
});

const handleOpen = (key: string, keyPath: string[]) => {
  console.log(key, keyPath);
};
const handleClose = (key: string, keyPath: string[]) => {
  console.log(key, keyPath);
};

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
      <el-aside :width="isCollapse ? '64px' : '200px'" class="main-aside">
        <div class="toggle-button" :class="{ 'collapsed': isCollapse }" @click="toggleCollapse">
          <el-icon v-if="isCollapse"><Expand /></el-icon>
          <el-icon v-else><Fold /></el-icon>
        </div>
        <el-menu
          :default-active="activeMenu"
          :default-openeds="defaultOpeneds"
          class="el-menu-vertical-demo"
          :collapse="isCollapse"
          @open="handleOpen"
          @close="handleClose"
          router
        >
          <el-menu-item index="/">
            <el-icon><icon-menu /></el-icon>
            <template #title>首页</template>
          </el-menu-item>

          <el-sub-menu index="tools">
            <template #title>
              <el-icon><Box /></el-icon>
              <span>综合工具</span>
            </template>
            <el-menu-item index="/tools/password-generator">随机密码</el-menu-item>
            <el-menu-item index="/tools/infinite-canvas">无限画布</el-menu-item>
          </el-sub-menu>
          
          <el-sub-menu index="game-tools">
            <template #title>
              <el-icon><MagicStick /></el-icon>
              <span>游戏工具</span>
            </template>
            <el-sub-menu index="fanxiu">
              <template #title>
                <el-icon><Lightning /></el-icon>
                <span>凡修手游</span>
              </template>
              <el-menu-item index="/fanxiu/calculator">兽魂计算器</el-menu-item>
              <el-menu-item index="/fanxiu/recharge">充值礼包(Beta)</el-menu-item>
            </el-sub-menu>
            <el-menu-item index="/dsp/calculator">
              <el-icon><Sunny /></el-icon>
              <span>戴森球计划</span>
            </el-menu-item>
            <el-sub-menu index="magic-craft">
              <template #title>
                <el-icon><Star /></el-icon>
                <span>魔法工艺</span>
              </template>
              <el-menu-item index="/magic-craft/xor-matrix">点灯解谜</el-menu-item>
            </el-sub-menu>
          </el-sub-menu>

          <el-menu-item index="/cluster" v-if="userStore.isAuthenticated">
            <el-icon><Monitor /></el-icon>
            <template #title>集群管理</template>
          </el-menu-item>
        </el-menu>
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
  height: 100vh;
  display: flex;
  width: 100%; /* Ensure full width */
}
.el-container {
  height: 100%;
  width: 100%;
}
.el-aside {
  background-color: #f5f7fa;
  border-right: 1px solid #e6e6e6;
  transition: width 0.3s;
  overflow-x: hidden;
  display: flex;
  flex-direction: column;
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
}
.el-menu {
  border-right: none;
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
}
</style>
