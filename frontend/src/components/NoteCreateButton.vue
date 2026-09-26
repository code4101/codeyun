<script setup lang="ts">
defineEmits<{ create: [format: 'html' | 'plate'] }>()
</script>
<template>
  <el-dropdown trigger="click" @command="format => $emit('create', format)">
    <el-button type="primary" size="small">新建节点 ▾</el-button>
    <template #dropdown><el-dropdown-menu>
      <el-dropdown-item command="html">HTML 富文本</el-dropdown-item>
      <el-dropdown-item command="plate">Plate 富文本（PG）</el-dropdown-item>
    </el-dropdown-menu></template>
  </el-dropdown>
</template>
