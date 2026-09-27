<template>
  <!-- 所有阅读格式的目录和大纲共用此容器；树组件只负责行内容及层级缩进。 -->
  <section class="reader-navigation-panel"><slot /></section>
</template>

<style scoped>
.reader-navigation-panel { box-sizing: border-box; display: flex; flex: 1; flex-direction: column; gap: 4px; min-width: 0; min-height: 0; padding: 4px; overflow: hidden; background: var(--reader-panel); }
.reader-navigation-panel > :slotted(p) { font-size: 12px; color: var(--reader-muted); }
</style>
