<script setup lang="ts">
import type { Component } from 'vue'
import {
  Box,
  Calendar,
  ChatDotRound,
  Document,
  MagicStick,
  Menu as IconMenu,
  Message,
  Monitor,
  Setting,
} from '@element-plus/icons-vue'

import type { AppDirectoryNode } from '@/features/access/appDirectory'
import { getDirectoryNodeEntryPath, isDirectorySubmenu } from '@/features/access/appDirectory'
import type { FeatureDirectoryMenuIcon } from '@/features/access/permissionRegistryTypes'

defineOptions({ name: 'AppDirectoryMenuNode' })

const props = defineProps<{
  node: AppDirectoryNode
}>()

const emit = defineEmits<{
  titleNavigate: [path: string, event: MouseEvent]
}>()

const iconComponents: Record<FeatureDirectoryMenuIcon, Component> = {
  home: IconMenu,
  tools: Box,
  ai: ChatDotRound,
  attendance: Calendar,
  game: MagicStick,
  notes: Document,
  cluster: Monitor,
  admin: Setting,
  contact: Message,
}

const handleTitleClick = (event: MouseEvent) => {
  const path = getDirectoryNodeEntryPath(props.node)
  if (path) {
    emit('titleNavigate', path, event)
  }
}
</script>

<template>
  <template v-if="node.menuItemsInline">
    <el-menu-item
      v-for="item in node.menuItems"
      :key="item.path"
      :index="item.path"
    >
      <el-icon v-if="node.icon"><component :is="iconComponents[node.icon]" /></el-icon>
      <template #title>{{ item.title }}</template>
    </el-menu-item>
    <AppDirectoryMenuNode
      v-for="child in node.children"
      :key="child.key"
      :node="child"
      @title-navigate="(path, event) => emit('titleNavigate', path, event)"
    />
  </template>

  <el-sub-menu v-else-if="isDirectorySubmenu(node)" :index="node.key">
    <template #title>
      <el-icon v-if="node.icon"><component :is="iconComponents[node.icon]" /></el-icon>
      <span
        v-if="getDirectoryNodeEntryPath(node)"
        class="menu-submenu-route-title"
        @click.stop="handleTitleClick"
      >
        {{ node.title }}
      </span>
      <span v-else>{{ node.title }}</span>
    </template>
    <el-menu-item
      v-for="item in node.menuItems.length > 1 ? node.menuItems : []"
      :key="item.path"
      :index="item.path"
    >
      {{ item.title }}
    </el-menu-item>
    <AppDirectoryMenuNode
      v-for="child in node.children"
      :key="child.key"
      :node="child"
      @title-navigate="(path, event) => emit('titleNavigate', path, event)"
    />
  </el-sub-menu>

  <el-menu-item v-else-if="node.menuItems[0]" :index="node.menuItems[0].path">
    <el-icon v-if="node.icon"><component :is="iconComponents[node.icon]" /></el-icon>
    <template #title>{{ node.menuItems[0].title }}</template>
  </el-menu-item>
</template>
