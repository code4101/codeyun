<script setup lang="ts">
import { ref } from 'vue';
import { useRoute } from 'vue-router';
import {
  Document,
  Menu as IconMenu,
  Location,
  Setting,
} from '@element-plus/icons-vue';

const route = useRoute();
const isCollapse = ref(false);

const handleOpen = (key: string, keyPath: string[]) => {
  console.log(key, keyPath);
};
const handleClose = (key: string, keyPath: string[]) => {
  console.log(key, keyPath);
};
</script>

<template>
  <div class="common-layout">
    <el-container>
      <el-aside width="200px">
        <el-menu
          default-active="2"
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
          
          <el-menu-item index="/cluster">
            <el-icon><setting /></el-icon>
            <template #title>集群管理</template>
          </el-menu-item>

          <el-sub-menu index="1">
            <template #title>
              <el-icon><location /></el-icon>
              <span>工具箱</span>
            </template>
            <el-menu-item-group>
              <template #title>文件系统</template>
              <el-menu-item index="/tools/file-explorer">文件浏览器</el-menu-item>
              <el-menu-item index="/tools/command-runner">命令运行器</el-menu-item>
            </el-menu-item-group>
            <el-menu-item-group title="实用工具">
              <el-menu-item index="/tools/demo">演示工具</el-menu-item>
            </el-menu-item-group>
          </el-sub-menu>
          
          <el-menu-item index="/about">
            <el-icon><document /></el-icon>
            <template #title>关于</template>
          </el-menu-item>
        </el-menu>
      </el-aside>
      <el-container>
        <el-header>
          <div class="header-content">
            <h2>CodeYun - 个人工具箱</h2>
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
  padding: 0 20px; /* Adjust padding */
}
.el-main {
  padding: 0; /* Remove default padding to allow children to control layout */
  width: 100%;
}
.el-menu {
  border-right: none;
}
</style>
