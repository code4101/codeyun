<template>
  <div class="storage-manager">
    <div class="header">
      <h2>存储维护中心</h2>
      <el-button 
        type="primary" 
        @click="refreshData" 
        :loading="loading"
        icon="Refresh"
      >
        刷新数据
      </el-button>
    </div>

    <el-tabs v-model="activeTab" class="storage-tabs" @tab-change="handleTabChange">
      
      <!-- Tab 1: 仪表盘 (Dashboard) -->
      <el-tab-pane label="仪表盘" name="dashboard">
        <div class="dashboard-tab" v-loading="dashboardLoading">
          <!-- Key Metrics Cards -->
          <div class="metrics-row">
            <el-card shadow="hover" class="metric-card">
              <template #header><div class="card-header"><span>存储总占用</span></div></template>
              <div class="metric-value">{{ formatSize(dashboardStats?.total_size_bytes || 0) }}</div>
              <div class="metric-desc">包含所有上传文件</div>
            </el-card>
            <el-card shadow="hover" class="metric-card">
              <template #header><div class="card-header"><span>文件总数</span></div></template>
              <div class="metric-value">{{ dashboardStats?.total_file_count || 0 }}</div>
              <div class="metric-desc">个文件</div>
            </el-card>
            <el-card shadow="hover" class="metric-card">
              <template #header><div class="card-header"><span>笔记总数</span></div></template>
              <div class="metric-value">{{ dashboardStats?.total_note_count || 0 }}</div>
              <div class="metric-desc">篇笔记</div>
            </el-card>
            <el-card shadow="hover" class="metric-card health-card">
              <template #header><div class="card-header"><span>系统健康度</span></div></template>
              <div class="metric-value health-score">{{ dashboardStats?.health_score || '-' }}</div>
              <el-progress 
                :percentage="dashboardStats?.health_score || 0" 
                :status="getHealthStatus(dashboardStats?.health_score)"
                :show-text="false"
              />
            </el-card>
          </div>

          <!-- Quick Actions / Insights Placeholder -->
          <div class="dashboard-content">
            <el-alert
              title="系统运行正常"
              type="success"
              description="定期检查存储健康状况有助于保持系统高性能运行。"
              show-icon
              :closable="false"
              class="mb-4"
            />
            
            <div class="quick-links">
              <h3>快捷操作</h3>
              <div class="links-row">
                <el-button @click="activeTab = 'analysis'">查看大文件</el-button>
                <el-button @click="activeTab = 'maintenance'">清理孤儿文件</el-button>
              </div>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- Tab 2: 资源分析 (Analysis) -->
      <el-tab-pane label="资源分析" name="analysis">
        <div class="analysis-tab" v-loading="analysisLoading">
          <div class="analysis-row">
            <!-- Top 50 Files -->
            <div class="analysis-section">
              <div class="section-title">Top 50 大文件 (Static Uploads)</div>
              <div class="analysis-table">
                <AnalysisTable 
                  :data="analysis?.top_files || []" 
                  type="file" 
                  allow-optimize 
                  @refresh="refreshData"
                />
              </div>
            </div>

            <!-- Top 50 Nodes -->
            <div class="analysis-section">
              <div class="section-title">Top 50 大节点 (Content Size)</div>
              <div class="analysis-table">
                <AnalysisTable :data="analysis?.top_nodes || []" type="node" />
              </div>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- Tab 3: 维护治理 (Maintenance) -->
      <el-tab-pane label="维护治理" name="maintenance">
        <div class="maintenance-tab" v-loading="maintenanceLoading">
          
          <!-- Configuration Section (Top Bar) -->
          <el-card class="config-card mb-4" shadow="never">
            <template #header>
              <div class="card-header">
                <span>自动化配置</span>
                <el-button type="primary" link @click="saveSchedule" :loading="savingSchedule">保存配置</el-button>
              </div>
            </template>
            <div class="config-form">
              <el-form :inline="true" :model="scheduleConfig" size="small">
                <el-form-item label="启用定期分析">
                  <el-switch v-model="scheduleConfig.enabled" />
                </el-form-item>
                <el-form-item label="执行计划 (Cron)">
                  <el-input v-model="scheduleConfig.cron_expression" placeholder="0 3 * * *" style="width: 150px" />
                </el-form-item>
                <el-form-item>
                   <el-tooltip content="Cron 格式: 分 时 日 月 周 (例如: 0 3 * * * 表示每天凌晨3点)" placement="top">
                    <el-icon><QuestionFilled /></el-icon>
                   </el-tooltip>
                </el-form-item>
              </el-form>
            </div>
          </el-card>

          <div class="maintenance-row">
            <!-- Orphan Cleaning -->
            <div class="maintenance-section">
              <div class="section-title">孤儿文件清理</div>
              <div class="maintenance-content">
                 <div class="actions-bar">
                    <div class="stats-text" v-if="maintenanceStatus">
                      发现 <span class="highlight">{{ maintenanceStatus.orphan_count }}</span> 个，
                      共 <span class="highlight">{{ formatSize(maintenanceStatus.orphan_size) }}</span>
                    </div>
                    <el-button 
                      type="danger" 
                      size="small"
                      @click="confirmDelete" 
                      :disabled="!orphans || orphans.length === 0"
                      :loading="deleting"
                      icon="Delete"
                    >
                      清理
                    </el-button>
                 </div>
                 <div class="table-wrapper">
                    <AnalysisTable :data="orphans" type="file" />
                 </div>
              </div>
            </div>

            <!-- Dead Links -->
            <div class="maintenance-section">
              <div class="section-title">死链修复</div>
              <div class="maintenance-content">
                 <!-- Fixable Alert -->
                 <div v-if="maintenanceStatus?.fixable_links?.length" class="mb-2">
                    <el-alert
                      :title="`可修复: ${maintenanceStatus.fixable_links.length}`"
                      type="warning"
                      show-icon
                      :closable="false"
                      size="small"
                    >
                      <template #default>
                        <el-button type="primary" size="small" link @click="handleFixLinks" :loading="fixing">
                          立即修复全部
                        </el-button>
                      </template>
                    </el-alert>
                 </div>
                 
                 <div class="table-wrapper">
                    <AnalysisTable :data="maintenanceStatus?.dead_links || []" type="link" />
                 </div>
              </div>
            </div>
          </div>
        </div>
      </el-tab-pane>

    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { QuestionFilled } from '@element-plus/icons-vue';
import AnalysisTable from './components/AnalysisTable.vue';
import { 
  fetchStorageDashboard,
  fetchStorageAnalysis,
  fetchMaintenanceStatus,
  fetchOrphanImages,
  deleteOrphanImages,
  fetchScheduleConfig,
  updateScheduleConfig,
  fixBrokenLinks,
  StorageDashboardStats,
  StorageAnalysisResponse,
  MaintenanceStatusResponse,
  ScheduleConfig,
  OrphanImage
} from '@/api/admin';

const activeTab = ref('dashboard');
const loading = ref(false);

// Dashboard Data
const dashboardStats = ref<StorageDashboardStats | null>(null);
const dashboardLoading = ref(false);

// Analysis Data
const analysis = ref<StorageAnalysisResponse | null>(null);
const analysisLoading = ref(false);

// Maintenance Data
const maintenanceStatus = ref<MaintenanceStatusResponse | null>(null);
const orphans = ref<OrphanImage[]>([]);
const maintenanceLoading = ref(false);
const deleting = ref(false);
const fixing = ref(false);

// Schedule Config
const scheduleConfig = ref<ScheduleConfig>({ enabled: false, cron_expression: '0 3 * * *' });
const savingSchedule = ref(false);

// Formatters
const formatSize = (bytes: number) => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};

const formatTime = (timestamp: number) => {
  return new Date(timestamp * 1000).toLocaleString();
};

const getHealthStatus = (score: number = 100) => {
  if (score >= 90) return 'success';
  if (score >= 60) return 'warning';
  return 'exception';
};

// Loaders
const loadDashboard = async () => {
  dashboardLoading.value = true;
  try {
    dashboardStats.value = await fetchStorageDashboard();
  } catch (error) {
    ElMessage.error('加载仪表盘失败');
  } finally {
    dashboardLoading.value = false;
  }
};

const loadAnalysis = async () => {
  analysisLoading.value = true;
  try {
    analysis.value = await fetchStorageAnalysis();
  } catch (error) {
    ElMessage.error('加载分析数据失败');
  } finally {
    analysisLoading.value = false;
  }
};

const loadMaintenance = async () => {
  maintenanceLoading.value = true;
  try {
    const [status, orphansData, config] = await Promise.all([
      fetchMaintenanceStatus(),
      fetchOrphanImages(), // Still fetch full list for deletion
      fetchScheduleConfig()
    ]);
    maintenanceStatus.value = status;
    orphans.value = orphansData.orphans;
    scheduleConfig.value = config;
  } catch (error) {
    ElMessage.error('加载维护数据失败');
  } finally {
    maintenanceLoading.value = false;
  }
};

const refreshData = () => {
  if (activeTab.value === 'dashboard') loadDashboard();
  else if (activeTab.value === 'analysis') loadAnalysis();
  else if (activeTab.value === 'maintenance') loadMaintenance();
};

const handleTabChange = (tabName: string | number) => {
  const normalizedName = String(tabName);
  if (normalizedName === 'dashboard' && !dashboardStats.value) loadDashboard();
  if (normalizedName === 'analysis' && !analysis.value) loadAnalysis();
  if (normalizedName === 'maintenance' && !maintenanceStatus.value) loadMaintenance();
};

// Actions
const saveSchedule = async () => {
  savingSchedule.value = true;
  try {
    await updateScheduleConfig(scheduleConfig.value);
    ElMessage.success('配置已保存');
  } catch (error) {
    ElMessage.error('保存配置失败，请检查 Cron 格式');
  } finally {
    savingSchedule.value = false;
  }
};

const handleFixLinks = async () => {
  fixing.value = true;
  try {
    const res = await fixBrokenLinks();
    ElMessage.success(res.message);
    loadMaintenance(); // Refresh
  } catch (error) {
    ElMessage.error('修复失败');
  } finally {
    fixing.value = false;
  }
};

const confirmDelete = () => {
  if (orphans.value.length === 0) return;
  ElMessageBox.confirm(
    `确定要删除 ${orphans.value.length} 个孤儿文件吗？此操作不可恢复。`,
    '警告',
    { confirmButtonText: '确定删除', cancelButtonText: '取消', type: 'warning' }
  ).then(async () => {
    deleting.value = true;
    try {
      // Collect all filenames
      const filenames = orphans.value.map(o => o.filename);
      // Batch delete? The API takes a list.
      // If list is too huge, might need chunking. Assuming < 1000 is fine.
      const result = await deleteOrphanImages(filenames);
      if (result.errors && result.errors.length > 0) {
          console.error("Delete errors:", result.errors);
          ElMessage.warning(`部分删除成功: ${result.deleted_count} 个文件，${result.errors.length} 个失败`);
      } else {
          ElMessage.success(`成功删除 ${result.deleted_count} 个文件`);
      }
      loadMaintenance();
    } catch (error: any) {
      console.error("Delete failed:", error);
      ElMessage.error(error.response?.data?.detail || '删除失败，请查看控制台');
    } finally {
      deleting.value = false;
    }
  }).catch(() => {});
};

onMounted(() => {
  loadDashboard();
});
</script>

<style scoped>
.storage-manager {
  padding: 20px;
  height: 100%;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  overflow: hidden;
  background-color: #f5f7fa;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.storage-tabs {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background-color: white;
  padding: 20px;
  border-radius: 8px;
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.1);
}

:deep(.el-tabs__content) {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

:deep(.el-tab-pane) {
  height: 100%;
}

/* Dashboard Styles */
.dashboard-tab {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.metrics-row {
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
}

.metric-card {
  flex: 1;
  min-width: 200px;
  text-align: center;
}

.card-header {
  font-weight: bold;
  color: #606266;
}

.metric-value {
  font-size: 28px;
  font-weight: bold;
  color: #303133;
  margin: 10px 0;
}

.metric-desc {
  font-size: 12px;
  color: #909399;
}

.health-score {
  color: #67C23A;
}

/* Analysis Styles */
.analysis-tab {
  height: 100%;
}

.analysis-row {
  display: flex;
  gap: 20px;
  height: 100%;
}

.analysis-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.section-title {
  font-size: 16px;
  font-weight: bold;
  margin-bottom: 12px;
  padding-left: 10px;
  border-left: 4px solid #409EFF;
}

.analysis-table {
  flex: 1;
  min-height: 0;
  border: 1px solid #EBEEF5;
  border-radius: 4px;
}

/* Maintenance Styles */
.maintenance-tab {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.config-card {
  margin-bottom: 20px;
}

.config-form {
  padding-top: 10px;
}

.inner-tabs {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.maintenance-row {
  display: flex;
  gap: 20px;
  flex: 1;
  min-height: 0;
}

.maintenance-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.maintenance-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  border: 1px solid #EBEEF5;
  border-radius: 4px;
  padding: 10px;
  background: white;
}

.table-wrapper {
  flex: 1;
  min-height: 0;
}

.actions-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  background: #fdf6ec;
  padding: 10px 15px;
  border-radius: 4px;
  border: 1px solid #faecd8;
}

.stats-text {
  color: #e6a23c;
  font-size: 14px;
}

.highlight {
  font-weight: bold;
  color: #d9001b;
}

.mb-4 { margin-bottom: 16px; }
.mt-2 { margin-top: 8px; }
</style>
