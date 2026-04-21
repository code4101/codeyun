<template>
  <div class="draw-calculator-container">
    <div class="header-section">
      <h2 class="title">凡修活动抽数计算</h2>
      <div class="header-actions">
        <el-button type="primary" link @click="resetToDefault">恢复默认规则</el-button>
      </div>
    </div>

    <div class="layout-container">
      <!-- Left Panel: Configuration -->
      <div class="left-panel">
        <el-card class="config-card">
          <template #header>
            <div class="card-header">
              <span>基础设置</span>
            </div>
          </template>
          
          <el-form label-position="top">
            <el-form-item label="当前拥有抽数 (直接抽)">
              <el-input v-model="initialDrawsStr" placeholder="支持输入算式，如 192+30*2">
                <template #append>
                  = {{ parsedInitialDraws }}
                </template>
              </el-input>
            </el-form-item>
          </el-form>
        </el-card>

        <el-card class="config-card rules-card">
          <template #header>
            <div class="card-header">
              <span>赠送规则配置</span>
              <el-button type="primary" size="small" icon="Plus" circle @click="addRule" />
            </div>
          </template>

          <div class="rules-list">
            <div v-for="(rule, index) in rules" :key="index" class="rule-item">
              <div class="rule-header">
                <span class="rule-index">规则 #{{ index + 1 }}</span>
                <el-button type="danger" link icon="Delete" @click="removeRule(index)" v-if="rules.length > 1" />
              </div>
              <div class="rule-body">
                <el-row :gutter="10">
                  <el-col :span="8">
                    <div class="label">截止抽数</div>
                    <el-input-number v-model="rule.maxDraws" size="small" :controls="false" style="width: 100%" />
                  </el-col>
                  <el-col :span="8">
                    <div class="label">每满(抽)</div>
                    <el-input-number v-model="rule.step" size="small" :controls="false" style="width: 100%" />
                  </el-col>
                  <el-col :span="8">
                    <div class="label">赠送(抽)</div>
                    <el-input-number v-model="rule.reward" size="small" :controls="false" style="width: 100%" />
                  </el-col>
                </el-row>
              </div>
            </div>
          </div>
        </el-card>

        <el-button type="primary" size="large" class="btn-calc" @click="calculate" :loading="calculating">
          开始计算
        </el-button>
      </div>

      <!-- Right Panel: Results -->
      <div class="right-panel">
        <el-card class="result-card" shadow="hover">
          <div class="result-summary">
            <div class="summary-item">
              <div class="label">最终累计抽数</div>
              <div class="value highlight">{{ result.totalDraws }}</div>
            </div>
            <div class="summary-divider"></div>
            <div class="summary-item">
              <div class="label">白嫖赠送抽数</div>
              <div class="value">{{ result.totalFree }}</div>
            </div>
          </div>
        </el-card>

        <el-card class="log-card" v-if="result.logs.length > 0">
          <template #header>
            <div class="card-header">
              <span>计算推演过程</span>
            </div>
          </template>
          <el-scrollbar height="400px">
            <ul class="log-list">
              <li v-for="(log, idx) in result.logs" :key="idx" class="log-item">
                <span class="log-step">[{{ log.step }}抽]</span>
                <span class="log-content">{{ log.msg }}</span>
                <span class="log-stock">剩余库存: {{ log.stock }}</span>
              </li>
            </ul>
          </el-scrollbar>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, computed, watch } from 'vue';
import { ElMessage } from 'element-plus';

interface Rule {
  maxDraws: number;
  step: number;
  reward: number;
}

interface LogEntry {
  step: number;
  msg: string;
  stock: number;
}

const STORAGE_KEY = 'fanxiu_draw_calculator_data';

const initialDrawsStr = ref('192');
const calculating = ref(false);

const parsedInitialDraws = computed(() => {
  try {
    // Basic sanitization: only allow digits, +, -, *, /, (, ), ., and spaces
    const sanitized = initialDrawsStr.value.replace(/[^0-9+\-*/().\s]/g, '');
    if (!sanitized) return 0;
    // Use Function constructor for safer evaluation than eval (though still risky if input not sanitized)
    const result = new Function(`return ${sanitized}`)();
    const num = Number(result);
    return isNaN(num) ? 0 : Math.floor(num);
  } catch (e) {
    return 0;
  }
});

const DEFAULT_RULES: Rule[] = [
  { maxDraws: 200, step: 20, reward: 4 },
  { maxDraws: 600, step: 40, reward: 6 },
  { maxDraws: 1400, step: 80, reward: 8 }
];

const rules = ref<Rule[]>(JSON.parse(JSON.stringify(DEFAULT_RULES)));

const result = reactive({
  totalDraws: 0,
  totalFree: 0,
  logs: [] as LogEntry[]
});

const saveData = () => {
  const data = {
    initialDrawsStr: initialDrawsStr.value,
    rules: rules.value
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
};

const loadData = () => {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (raw) {
    try {
      const data = JSON.parse(raw);
      if (data.initialDrawsStr) initialDrawsStr.value = data.initialDrawsStr;
      if (data.rules && Array.isArray(data.rules)) rules.value = data.rules;
      return true;
    } catch (e) {
      console.error('Failed to load data', e);
    }
  }
  return false;
};

const resetToDefault = () => {
  rules.value = JSON.parse(JSON.stringify(DEFAULT_RULES));
  initialDrawsStr.value = '192';
  saveData(); // Explicitly save default state
  calculate();
  ElMessage.success('已恢复默认规则');
};

const addRule = () => {
  const lastRule = rules.value[rules.value.length - 1];
  rules.value.push({
    maxDraws: lastRule ? lastRule.maxDraws + 400 : 200,
    step: 20,
    reward: 1
  });
};

const removeRule = (index: number) => {
  rules.value.splice(index, 1);
};

// Watch for changes and auto-save
watch(initialDrawsStr, () => {
  saveData();
});

watch(rules, () => {
  saveData();
}, { deep: true });

const calculate = () => {
  calculating.value = true;
  result.logs = [];
  
  // Sort rules by maxDraws to ensure correct order
  const sortedRules = [...rules.value].sort((a, b) => a.maxDraws - b.maxDraws);
  
  let currentStock = parsedInitialDraws.value;
  let totalDraws = 0;
  let totalFree = 0;
  
  // Simulation loop
  // Safety break to prevent infinite loops if rules are broken (e.g. step=1, reward=2)
  const MAX_LOOPS = 10000; 
  let loops = 0;

  while (currentStock > 0 && loops < MAX_LOOPS) {
    loops++;
    
    // Consume 1 draw
    currentStock--;
    totalDraws++;
    
    // Check rules
    // Find the applicable rule for the current totalDraws
    // A rule applies if totalDraws <= maxDraws. 
    // Since rules are sorted, we take the first one that matches.
    // However, we need to be careful about ranges. 
    // Usually "Before 200 draws" means 1-200. "Before 600 draws" means 201-600?
    // Based on user description: "200抽以前... 600抽以前..." usually implies segments.
    // Let's assume standard game logic: 
    // If I'm at 220 draws, the "200 rule" no longer applies, the "600 rule" applies.
    
    let activeRule: Rule | undefined;
    
    // Find the rule that covers the CURRENT totalDraws
    for (const rule of sortedRules) {
      if (totalDraws <= rule.maxDraws) {
        activeRule = rule;
        break;
      }
    }
    
    if (activeRule) {
      if (totalDraws % activeRule.step === 0) {
        currentStock += activeRule.reward;
        totalFree += activeRule.reward;
        result.logs.push({
          step: totalDraws,
          msg: `触发规则[每${activeRule.step}送${activeRule.reward}]`,
          stock: currentStock
        });
      }
    }
  }

  result.totalDraws = totalDraws;
  result.totalFree = totalFree;
  calculating.value = false;
  
  if (loops >= MAX_LOOPS) {
    ElMessage.warning('计算次数过多，可能触发了无限循环规则，已强制停止');
  } else {
    ElMessage.success('计算完成');
  }
};

onMounted(() => {
  if (!loadData()) {
    // If no data found, calculate with defaults
    calculate();
  } else {
    // If data loaded, calculate with loaded data
    calculate();
  }
});
</script>

<style scoped>
.draw-calculator-container {
  padding: 20px;
  max-width: 1200px;
  margin: 0 auto;
}

.header-section {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  border-bottom: 1px solid #ebeef5;
  padding-bottom: 15px;
}

.title {
  font-size: 24px;
  font-weight: bold;
  color: #303133;
  margin: 0;
}

.layout-container {
  display: flex;
  gap: 20px;
  align-items: flex-start;
}

.left-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.right-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.config-card {
  border-radius: 8px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: bold;
}

.rules-list {
  display: flex;
  flex-direction: column;
  gap: 15px;
}

.rule-item {
  background: #f5f7fa;
  padding: 10px;
  border-radius: 6px;
  border: 1px solid #ebeef5;
}

.rule-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 5px;
}

.rule-index {
  font-size: 12px;
  color: #909399;
  font-weight: bold;
}

.label {
  font-size: 12px;
  color: #606266;
  margin-bottom: 4px;
}

.btn-calc {
  width: 100%;
  font-weight: bold;
  letter-spacing: 1px;
}

.result-card {
  background: linear-gradient(135deg, #f0f9eb 0%, #e1f3d8 100%);
  border: 1px solid #c2e7b0;
}

.result-summary {
  display: flex;
  justify-content: space-around;
  align-items: center;
  padding: 10px 0;
}

.summary-item {
  text-align: center;
}

.summary-item .label {
  font-size: 14px;
  color: #529b2e;
  margin-bottom: 5px;
}

.summary-item .value {
  font-size: 32px;
  font-weight: bold;
  color: #303133;
}

.summary-item .value.highlight {
  color: #67c23a;
  font-size: 42px;
}

.summary-divider {
  width: 1px;
  height: 40px;
  background: #c2e7b0;
}

.log-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.log-item {
  display: flex;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: 1px solid #ebeef5;
  font-size: 14px;
}

.log-item:last-child {
  border-bottom: none;
}

.log-step {
  color: #909399;
  width: 60px;
}

.log-content {
  flex: 1;
  color: #606266;
}

.log-stock {
  color: #409eff;
  font-weight: bold;
}

@media (max-width: 768px) {
  .layout-container {
    flex-direction: column;
  }
}
</style>
