
<template>
  <div class="iframe-container">
    <iframe src="/dsp-calc/index.html" frameborder="0" width="100%" height="100%"></iframe>
  </div>
</template>

<style scoped>
.iframe-container {
  /* 减去头部和可能的 padding，确保铺满剩余空间 */
  height: 100%;
  width: 100%;
  overflow: hidden;
}
</style>
