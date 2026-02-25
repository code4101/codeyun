<template>
  <div class="password-generator">
    <el-card class="box-card">
      <template #header>
        <div class="card-header">
          <h2>
            <el-icon><Key /></el-icon> 随机密码生成
          </h2>
        </div>
      </template>

      <el-form label-width="120px">
        <!-- Character Options -->
        <el-form-item label="所用字符">
          <el-checkbox v-model="options.useLower" label="a-z" />
          <el-checkbox v-model="options.useUpper" label="A-Z" />
          <el-checkbox v-model="options.useNumbers" label="0-9" />
          <div class="special-chars-group">
            <el-checkbox v-model="options.useSpecial" label="特殊字符" />
            <el-input 
              v-model="options.specialChars" 
              placeholder="!@#$%" 
              style="width: 150px; margin-left: 10px;"
              :disabled="!options.useSpecial"
            />
          </div>
        </el-form-item>

        <!-- Exclude Characters -->
        <el-form-item label="排除字符">
          <el-checkbox v-model="options.excludeEnabled" label="启用" style="margin-right: 10px;" />
          <el-input 
            v-model="options.excludeChars" 
            placeholder="例如: iIl1oO0" 
            style="width: 300px;"
            :disabled="!options.excludeEnabled"
          />
        </el-form-item>

        <!-- Length and Quantity -->
        <el-form-item label="密码设置">
          <el-col :span="11">
            <el-form-item label="长度" label-width="60px">
              <el-input-number v-model="options.length" :min="4" :max="128" /> 位
            </el-form-item>
          </el-col>
          <el-col :span="2" class="text-center">
            <span class="text-gray-500"></span>
          </el-col>
          <el-col :span="11">
            <el-form-item label="数量" label-width="60px">
              <el-input-number v-model="options.count" :min="1" :max="50" /> 个
            </el-form-item>
          </el-col>
        </el-form-item>

        <!-- Generate Button -->
        <el-form-item>
          <el-button type="primary" @click="generatePasswords" size="large" style="width: 200px;">
            生成密码
          </el-button>
        </el-form-item>
      </el-form>

      <!-- Result Area -->
      <div v-if="generatedPasswords.length > 0" class="result-area">
        <div v-for="(pwd, index) in generatedPasswords" :key="index" class="password-item">
          <el-input 
            v-model="generatedPasswords[index]" 
            readonly 
            class="password-input"
          >
            <template #append>
              <el-button @click="copyToClipboard(pwd)">
                <el-icon><CopyDocument /></el-icon> 复制
              </el-button>
            </template>
          </el-input>
          <div class="password-strength">
            <el-tag :type="getStrengthType(pwd)">{{ getStrengthLabel(pwd) }}</el-tag>
            <span class="crack-time">破解耗时: {{ estimateCrackTime(pwd) }}</span>
          </div>
        </div>
      </div>

      <!-- History Section -->
      <div class="history-section">
        <div class="history-controls">
          <el-checkbox v-model="options.recordHistory">记录历史记录</el-checkbox>
          <el-button type="primary" link @click="toggleHistory">
            {{ showHistory ? '隐藏历史记录' : '查看历史记录' }}
            <el-icon class="el-icon--right">
              <ArrowUp v-if="showHistory" />
              <ArrowDown v-else />
            </el-icon>
          </el-button>
        </div>

        <el-collapse-transition>
          <div v-if="showHistory" class="history-list">
            <div class="history-header">
              <span>最近生成的 1000 个密码 (存储在本地)</span>
              <el-button type="danger" link @click="clearHistory">清空记录</el-button>
            </div>
            <el-table :data="history" style="width: 100%" height="300">
              <el-table-column prop="password" label="密码" />
              <el-table-column prop="date" label="时间" width="180" />
              <el-table-column label="操作" width="100">
                <template #default="scope">
                  <el-button size="small" @click="copyToClipboard(scope.row.password)">复制</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-collapse-transition>
      </div>

      <!-- Reference Section -->
      <div class="reference-section">
        <el-divider border-style="dashed" />
        <div class="reference-link">
          参考来源: <a href="https://suijimimashengcheng.bmcx.com/" target="_blank" rel="noopener noreferrer">随机密码生成 (suijimimashengcheng.bmcx.com)</a>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { Key, CopyDocument, ArrowDown, ArrowUp } from '@element-plus/icons-vue';

interface PasswordOptions {
  useLower: boolean;
  useUpper: boolean;
  useNumbers: boolean;
  useSpecial: boolean;
  specialChars: string;
  excludeEnabled: boolean;
  excludeChars: string;
  length: number;
  count: number;
  recordHistory: boolean;
}

interface HistoryItem {
  password: string;
  date: string;
}

const options = reactive<PasswordOptions>({
  useLower: true,
  useUpper: true,
  useNumbers: true,
  useSpecial: true,
  specialChars: '!@#$%',
  excludeEnabled: false,
  excludeChars: 'iIl1oO0',
  length: 16,
  count: 1,
  recordHistory: true,
});

const generatedPasswords = ref<string[]>([]);
const history = ref<HistoryItem[]>([]);
const showHistory = ref(false);

// Load history from localStorage
onMounted(() => {
  const savedHistory = localStorage.getItem('password_history');
  if (savedHistory) {
    try {
      history.value = JSON.parse(savedHistory);
    } catch (e) {
      console.error('Failed to parse history', e);
    }
  }
});

// Save history to localStorage whenever it changes
watch(history, (newHistory) => {
  localStorage.setItem('password_history', JSON.stringify(newHistory));
}, { deep: true });

const generatePasswords = () => {
  let charset = '';
  if (options.useLower) charset += 'abcdefghijklmnopqrstuvwxyz';
  if (options.useUpper) charset += 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  if (options.useNumbers) charset += '0123456789';
  if (options.useSpecial) charset += options.specialChars;

  if (options.excludeEnabled && options.excludeChars) {
    // Remove excluded characters
    const excludeSet = new Set(options.excludeChars.split(''));
    charset = charset.split('').filter(c => !excludeSet.has(c)).join('');
  }

  if (charset.length === 0) {
    ElMessage.error('请至少选择一种字符类型，并确保可用字符集不为空');
    return;
  }

  const newPasswords: string[] = [];
  for (let i = 0; i < options.count; i++) {
    let password = '';
    const array = new Uint32Array(options.length);
    window.crypto.getRandomValues(array);
    
    for (let j = 0; j < options.length; j++) {
      password += charset[array[j] % charset.length];
    }
    newPasswords.push(password);
  }

  generatedPasswords.value = newPasswords;

  if (options.recordHistory) {
    const now = new Date().toLocaleString();
    const newHistoryItems = newPasswords.map(pwd => ({ password: pwd, date: now }));
    history.value = [...newHistoryItems, ...history.value].slice(0, 1000);
  }
};

const copyToClipboard = async (text: string) => {
  try {
    await navigator.clipboard.writeText(text);
    ElMessage.success('复制成功');
  } catch (err) {
    ElMessage.error('复制失败');
  }
};

const toggleHistory = () => {
  showHistory.value = !showHistory.value;
};

const clearHistory = () => {
  history.value = [];
  localStorage.removeItem('password_history');
  ElMessage.success('历史记录已清空');
};

// Strength estimation logic
const getStrengthScore = (pwd: string): number => {
  let score = 0;
  if (!pwd) return 0;
  
  // Length contribution
  score += pwd.length * 4;
  
  // Character set contribution
  const hasLower = /[a-z]/.test(pwd);
  const hasUpper = /[A-Z]/.test(pwd);
  const hasNumber = /[0-9]/.test(pwd);
  const hasSpecial = /[^a-zA-Z0-9]/.test(pwd);
  
  const typeCount = (hasLower ? 1 : 0) + (hasUpper ? 1 : 0) + (hasNumber ? 1 : 0) + (hasSpecial ? 1 : 0);
  
  score += (typeCount - 1) * 10;
  
  // Bonus for mixing
  if (hasLower && hasUpper && hasNumber && hasSpecial) score += 15;
  
  return score;
};

const getStrengthType = (pwd: string): "success" | "warning" | "danger" | "info" => {
  const score = getStrengthScore(pwd);
  if (score >= 80) return 'success';
  if (score >= 60) return 'warning';
  return 'danger';
};

const getStrengthLabel = (pwd: string): string => {
  const score = getStrengthScore(pwd);
  if (score >= 80) return '非常安全';
  if (score >= 60) return '安全';
  if (score >= 40) return '一般';
  return '弱';
};

const estimateCrackTime = (pwd: string): string => {
  // Rough estimation logic for display purposes
  const score = getStrengthScore(pwd);
  if (score < 40) return '瞬间';
  if (score < 60) return '几分钟';
  if (score < 80) return '几天到几年';
  return '几百年甚至更久';
};
</script>

<style scoped>
.password-generator {
  padding: 20px;
  max-width: 800px;
  margin: 0 auto;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-header h2 {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0;
  font-size: 20px;
}

.special-chars-group {
  display: inline-flex;
  align-items: center;
  margin-left: 20px;
}

.result-area {
  margin-top: 20px;
  border-top: 1px solid #ebeef5;
  padding-top: 20px;
}

.password-item {
  margin-bottom: 15px;
}

.password-input :deep(.el-input__inner) {
  font-family: monospace;
  font-size: 16px;
  color: #2c3e50;
  font-weight: bold;
}

.password-strength {
  margin-top: 5px;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 14px;
}

.crack-time {
  color: #606266;
}

.history-section {
  margin-top: 30px;
  border-top: 1px solid #ebeef5;
  padding-top: 10px;
}

.history-controls {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.history-list {
  background: #f5f7fa;
  padding: 10px;
  border-radius: 4px;
}

.history-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
  font-size: 12px;
  color: #909399;
}

.reference-section {
  margin-top: 20px;
  text-align: center;
  font-size: 12px;
  color: #909399;
}

.reference-link a {
  color: #909399;
  text-decoration: none;
}

.reference-link a:hover {
  color: #409eff;
}
</style>
