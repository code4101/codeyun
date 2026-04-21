<template>
  <div class="standalone-layout">
    <div class="page-shell-main">
      <router-view />
    </div>
  </div>
</template>

<style scoped>
.standalone-layout {
  display: flex;
  flex-direction: column;
  min-height: 100dvh;
  height: 100dvh;
  background: #fff;
  overflow: hidden;
}

.page-shell-main {
  flex: 1;
  min-height: 0;
  width: 100%;
  overflow: auto;
}
</style>
