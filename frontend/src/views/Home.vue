<script setup lang="ts">
// 首页不再需要复杂的逻辑和 API 调用
</script>

<template>
  <div class="home-container">
    <div class="hero-section">
      <h1 class="main-title">CodeYun</h1>
      <p class="subtitle">code4101的百宝箱云平台</p>
      <div class="links-section">
        <el-link href="https://github.com/code4101/codeyun" target="_blank" type="primary" class="github-link">
          <el-icon class="link-icon"><Link /></el-icon>
          GitHub
        </el-link>
      </div>
    </div>
  </div>
</template>

<style scoped>
.home-container {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100%;
  text-align: center;
  padding: 40px;
}

.main-title {
  font-size: 48px;
  font-weight: bold;
  color: #303133;
  margin-bottom: 10px;
}

.subtitle {
  font-size: 24px;
  color: #606266;
  margin-bottom: 30px;
}

.links-section {
  margin-top: 20px;
}

.github-link {
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.link-icon {
  font-size: 20px;
}
</style>
