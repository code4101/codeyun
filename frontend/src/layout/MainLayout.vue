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
  MagicStick
} from '@element-plus/icons-vue';

const route = useRoute();
const router = useRouter();
const userStore = useUserStore();
const isCollapse = ref(false);

const activeMenu = computed(() => {
  if (route.path.startsWith('/cluster/logs/')) return '/cluster';
  return route.path;
});

const defaultOpeneds = computed(() => {
  if (route.path.startsWith('/fanxiu/')) return ['game-tools', 'fanxiu'];
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
      <el-aside width="200px">
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
          
          <el-sub-menu index="game-tools">
            <template #title>
              <el-icon><MagicStick /></el-icon>
              <span>游戏工具</span>
            </template>
            <el-sub-menu index="fanxiu">
              <template #title>
                <el-icon><Cellphone /></el-icon>
                <span>凡修手游</span>
              </template>
              <el-menu-item index="/fanxiu/calculator">兽魂计算器</el-menu-item>
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
}
</style>
