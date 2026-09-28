<script setup lang="ts">
import { Link, Message } from '@element-plus/icons-vue';
import { useFeatureAccessStore } from '@/store/featureAccessStore';

const featureAccessStore = useFeatureAccessStore();
</script>

<template>
  <div class="home-container">
    <div class="hero-section">
      <h1 class="main-title">CodeYun</h1>
      <p class="subtitle">code4101的百宝箱云平台</p>
      <div class="links-section">
        <el-link href="https://github.com/code4101/codeyun" target="_blank" rel="noopener noreferrer" type="primary" class="home-link">
          <el-icon class="link-icon"><Link /></el-icon>
          GitHub
        </el-link>
        <router-link v-if="featureAccessStore.isAllowed('author-contact')" to="/contact-author" class="home-link contact-link">
          <el-icon class="link-icon"><Message /></el-icon>
          联系作者
        </router-link>
      </div>
      <p class="disclaimer">免责声明：本站为个人实验项目，不对数据隐私及备份安全负责。请勿存储敏感信息，并定期自行备份。</p>
    </div>
  </div>
</template>

<style scoped>
.home-container {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100%;
  box-sizing: border-box;
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
  display: flex;
  justify-content: center;
  align-items: center;
  flex-wrap: wrap;
  gap: 24px;
}

.home-link {
  font-size: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}

.contact-link {
  color: var(--el-color-primary);
  text-decoration: none;
}

.contact-link:hover {
  text-decoration: underline;
}

.disclaimer {
  max-width: 480px;
  margin: 32px auto 0;
  color: #909399;
  font-size: 13px;
  line-height: 1.8;
}

.link-icon {
  font-size: 20px;
}
</style>
