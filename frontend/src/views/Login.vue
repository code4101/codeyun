<template>
  <div class="auth-container">
    <el-card class="auth-card">
      <template #header>
        <div class="card-header">
          <span>登录 CodeYun</span>
        </div>
      </template>
      
      <el-form 
        ref="loginFormRef"
        :model="form"
        :rules="rules"
        label-width="0"
        @submit.prevent="handleLogin"
      >
        <el-form-item prop="username">
          <el-input 
            v-model="form.username" 
            placeholder="用户名" 
            prefix-icon="User"
          />
        </el-form-item>
        
        <el-form-item prop="password">
          <el-input 
            v-model="form.password" 
            type="password" 
            placeholder="密码" 
            prefix-icon="Lock"
            show-password
            @keyup.enter="handleLogin"
          />
        </el-form-item>
        
        <el-alert
          v-if="userStore.error"
          :title="userStore.error"
          type="error"
          show-icon
          :closable="false"
          style="margin-bottom: 20px"
        />
        
        <el-button 
          type="primary" 
          :loading="userStore.loading" 
          class="submit-btn" 
          @click="handleLogin"
        >
          登录
        </el-button>
        
        <div class="auth-links">
          <router-link to="/register">没有账号？去注册</router-link>
        </div>
        
        <div class="disclaimer">
          <p>友情提示：本项目仅为个人实验性质，不对个人数据隐私及数据备份安全负责。请勿存储敏感信息，并定期自行备份重要数据。</p>
        </div>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { useUserStore } from '@/store/userStore';
import type { FormInstance, FormRules } from 'element-plus';

const router = useRouter();
const userStore = useUserStore();
const loginFormRef = ref<FormInstance>();

const form = reactive({
  username: '',
  password: '',
});

const rules = reactive<FormRules>({
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
});

const handleLogin = async () => {
  if (!loginFormRef.value) return;
  
  await loginFormRef.value.validate(async (valid) => {
    if (valid) {
      const success = await userStore.login(form.username, form.password);
      if (success) {
        const redirect = router.currentRoute.value.query.redirect as string;
        router.push(redirect || '/');
      }
    }
  });
};
</script>

<style scoped>
.auth-container {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100vh;
  background-color: #f5f7fa;
}

.auth-card {
  width: 100%;
  max-width: 400px;
}

.card-header {
  text-align: center;
  font-size: 18px;
  font-weight: bold;
}

.submit-btn {
  width: 100%;
}

.auth-links {
  margin-top: 15px;
  text-align: center;
  font-size: 14px;
}

.auth-links a {
  color: #409eff;
  text-decoration: none;
}

.auth-links a:hover {
  text-decoration: underline;
}

.disclaimer {
  margin-top: 30px;
  padding-top: 15px;
  border-top: 1px solid #f0f0f0;
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
  text-align: justify;
}
</style>
