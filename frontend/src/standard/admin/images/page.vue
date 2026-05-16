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
              <template #header><div class="card-header"><span>数据工作区</span></div></template>
              <div class="metric-value">{{ dataWorkspaceUsage ? formatSize(dataWorkspaceUsage.allocated_size_bytes) : '-' }}</div>
              <div class="metric-desc metric-desc-lines">
                <el-tag v-if="dataWorkspaceUsage" size="small" :type="healthTagType(dataWorkspaceUsage.health_status)">
                  健康 {{ dataWorkspaceUsage.health_score }}
                </el-tag>
                <span v-else>m2603codeyun</span>
                <span
                  v-if="dashboardStats"
                  class="metric-subset"
                  :title="dashboardStats.attachments_path || ''"
                >
                  含附件 {{ formatSize(dashboardStats.total_size_bytes) }} / {{ dashboardStats.total_file_count }} 个
                </span>
              </div>
            </el-card>
            <el-card shadow="hover" class="metric-card">
              <template #header><div class="card-header"><span>源码目录</span></div></template>
              <div class="metric-value">{{ sourceDirUsage ? formatSize(sourceDirUsage.allocated_size_bytes) : '-' }}</div>
              <div class="metric-desc">
                <el-tag v-if="sourceDirUsage" size="small" :type="healthTagType(sourceDirUsage.health_status)">
                  健康 {{ sourceDirUsage.health_score }}
                </el-tag>
                <span v-else>仅应存源码和依赖</span>
              </div>
            </el-card>
            <el-card shadow="hover" class="metric-card">
              <template #header><div class="card-header"><span>笔记总数</span></div></template>
              <div class="metric-value">{{ dashboardStats?.total_note_count || 0 }}</div>
              <div class="metric-desc">篇笔记</div>
            </el-card>
            <el-card shadow="hover" class="metric-card health-card">
              <template #header><div class="card-header"><span>目录健康度</span></div></template>
              <div class="metric-value health-score">{{ directoryHealthScore || '-' }}</div>
              <el-progress 
                :percentage="directoryHealthScore || 0" 
                :status="getHealthStatus(directoryHealthScore)"
                :show-text="false"
              />
            </el-card>
          </div>

          <!-- Quick Actions / Insights Placeholder -->
          <div class="dashboard-content">
            <section class="workspace-usage" v-loading="usageLoading">
              <div class="section-header">
                <div>
                  <div class="usage-title-row">
                    <h3>{{ activeUsage?.label || '目录' }} Top 占用</h3>
                    <el-radio-group v-model="activeUsageScope" size="small">
                      <el-radio-button value="data_workspace">数据工作区</el-radio-button>
                      <el-radio-button value="source_dir">源码目录</el-radio-button>
                    </el-radio-group>
                  </div>
                  <p v-if="activeUsage" class="workspace-path" :title="activeUsage.root_path">
                    {{ activeUsage.root_path }}
                  </p>
                  <p v-if="activeUsage" class="workspace-role">
                    {{ activeUsage.expected_role }}
                    <template v-if="activeUsage.scope === 'data_workspace' && dashboardStats">
                      ，当前附件 {{ formatSize(dashboardStats.total_size_bytes) }} 已纳入本工作区治理
                    </template>
                  </p>
                </div>
                <el-button size="small" @click="loadActiveUsage(true)" :loading="usageLoading">
                  重新统计
                </el-button>
              </div>
              <div v-if="activeUsage" class="workspace-summary">
                <span>
                  <strong>健康</strong>
                  <el-tag size="small" :type="healthTagType(activeUsage.health_status)">
                    {{ healthStatusLabel(activeUsage.health_status) }} {{ activeUsage.health_score }}
                  </el-tag>
                </span>
                <span><strong>磁盘占用</strong>{{ formatSize(activeUsage.allocated_size_bytes) }}</span>
                <span><strong>逻辑大小</strong>{{ formatSize(activeUsage.logical_size_bytes) }}</span>
                <span><strong>文件</strong>{{ activeUsage.file_count }}</span>
                <span><strong>目录</strong>{{ activeUsage.directory_count }}</span>
                <span v-if="activeUsage.inaccessible_count" class="summary-warning">
                  <strong>跳过</strong>{{ activeUsage.inaccessible_count }}
                </span>
                <span><strong>耗时</strong>{{ formatElapsed(activeUsage.elapsed_ms) }}</span>
                <span><strong>来源</strong>{{ usageSourceLabel(activeUsage.source) }}</span>
              </div>
              <div v-if="activeUsage" class="governance-stack">
                <div v-if="activeUsage.health_issues.length" class="governance-section">
                  <div class="mini-section-title">健康问题</div>
                  <el-table
                    :data="activeUsage.health_issues"
                    table-layout="auto"
                    :fit="false"
                    size="small"
                    class="health-table"
                  >
                    <el-table-column label="级别" width="84">
                      <template #default="scope">
                        <el-tag size="small" :type="severityTagType(scope.row.severity)">
                          {{ severityLabel(scope.row.severity) }}
                        </el-tag>
                      </template>
                    </el-table-column>
                    <el-table-column prop="title" label="问题" min-width="180" show-overflow-tooltip />
                    <el-table-column label="影响" width="110">
                      <template #default="scope">{{ scope.row.size_bytes ? formatSize(scope.row.size_bytes) : '-' }}</template>
                    </el-table-column>
                    <el-table-column prop="detail" label="判断依据" min-width="280" show-overflow-tooltip />
                  </el-table>
                </div>
                <div v-if="activeSlimmingCandidates.length" class="governance-section">
                  <div class="mini-section-title">瘦身候选</div>
                  <el-table
                    :data="activeSlimmingCandidates"
                    table-layout="auto"
                    :fit="false"
                    size="small"
                    class="health-table"
                  >
                    <el-table-column prop="title" label="对象" min-width="180" show-overflow-tooltip />
                    <el-table-column label="磁盘占用" width="110">
                      <template #default="scope">{{ formatSize(scope.row.allocated_size_bytes) }}</template>
                    </el-table-column>
                    <el-table-column label="风险" width="82">
                      <template #default="scope">
                        <el-tag size="small" :type="scope.row.risk === 'low' ? 'success' : 'warning'">
                          {{ scope.row.risk === 'low' ? '低' : '需确认' }}
                        </el-tag>
                      </template>
                    </el-table-column>
                    <el-table-column prop="detail" label="处理逻辑" min-width="260" show-overflow-tooltip />
                    <el-table-column label="操作" width="112" align="center">
                      <template #default="scope">
                        <el-button size="small" link type="primary" @click="handleSlimmingAction(scope.row)">
                          {{ scope.row.action_label || '查看' }}
                        </el-button>
                      </template>
                    </el-table-column>
                  </el-table>
                </div>
                <div v-if="!activeUsage.health_issues.length && !activeSlimmingCandidates.length" class="empty-inline">
                  暂无明显治理项
                </div>
              </div>
              <el-table
                v-if="activeUsage"
                :data="activeUsage.top_entries"
                table-layout="auto"
                :fit="false"
                size="small"
                class="workspace-table"
              >
                <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip />
                <el-table-column label="磁盘占用" width="120">
                  <template #default="scope">{{ formatSize(scope.row.allocated_size_bytes) }}</template>
                </el-table-column>
                <el-table-column label="逻辑大小" width="120">
                  <template #default="scope">{{ formatSize(scope.row.logical_size_bytes) }}</template>
                </el-table-column>
                <el-table-column prop="file_count" label="文件" width="80" />
                <el-table-column prop="directory_count" label="目录" width="80" />
                <el-table-column prop="path" label="路径" min-width="260" show-overflow-tooltip />
              </el-table>
              <el-empty v-else-if="!usageLoading" description="暂无目录统计" />
            </section>
            
            <div class="quick-links">
              <h3>快捷操作</h3>
              <div class="links-row">
                <el-button @click="activeTab = 'analysis'">查看大文件</el-button>
                <el-button @click="activeTab = 'maintenance'">附件孤儿清理</el-button>
                <el-button @click="openTreeSize">TreeSize 详查</el-button>
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
              <div class="section-title">附件 Top 50 大文件</div>
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
              <div class="section-title">笔记正文 Top 50 大节点</div>
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
                  <el-input v-model="scheduleConfig.cron_expression" placeholder="35 0 * * *" style="width: 150px" />
                </el-form-item>
                <el-form-item>
                   <el-tooltip content="Cron 格式: 分 时 日 月 周 (例如: 35 0 * * * 表示每天 00:35)" placement="top">
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
import { useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import { QuestionFilled } from '@element-plus/icons-vue';
import AnalysisTable from './components/AnalysisTable.vue';
import { 
  fetchStorageDashboard,
  fetchWorkspaceUsage,
  fetchStorageAnalysis,
  fetchMaintenanceStatus,
  fetchOrphanImages,
  deleteOrphanImages,
  fetchScheduleConfig,
  updateScheduleConfig,
  fixBrokenLinks,
  StorageDashboardStats,
  StorageUsageScope,
  WorkspaceUsageResponse,
  StorageAnalysisResponse,
  MaintenanceStatusResponse,
  ScheduleConfig,
  OrphanImage,
  StorageSlimmingCandidate
} from '@/api/admin';

const router = useRouter();
const activeTab = ref('dashboard');
const loading = ref(false);

// Dashboard Data
const dashboardStats = ref<StorageDashboardStats | null>(null);
const dashboardLoading = ref(false);
const activeUsageScope = ref<StorageUsageScope>('data_workspace');
const dataWorkspaceUsage = ref<WorkspaceUsageResponse | null>(null);
const sourceDirUsage = ref<WorkspaceUsageResponse | null>(null);
const usageLoadingByScope = ref<Record<StorageUsageScope, boolean>>({
  data_workspace: false,
  data_dir: false,
  source_dir: false,
});
const activeUsage = computed(() => (
  activeUsageScope.value === 'source_dir' ? sourceDirUsage.value : dataWorkspaceUsage.value
));
const usageLoading = computed(() => Boolean(usageLoadingByScope.value[activeUsageScope.value]));
const activeSlimmingCandidates = computed(() => activeUsage.value?.slimming_candidates.slice(0, 12) || []);
const directoryHealthScore = computed(() => {
  const scores = [dataWorkspaceUsage.value?.health_score, sourceDirUsage.value?.health_score]
    .filter((score): score is number => typeof score === 'number');
  if (!scores.length) return dashboardStats.value?.health_score || 0;
  return Math.min(...scores);
});

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
const scheduleConfig = ref<ScheduleConfig>({ enabled: false, cron_expression: '35 0 * * *' });
const savingSchedule = ref(false);

// Formatters
const formatSize = (bytes: number) => {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const i = Math.min(sizes.length - 1, Math.floor(Math.log(bytes) / Math.log(k)));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
};

const formatTime = (timestamp: number) => {
  return new Date(timestamp * 1000).toLocaleString();
};

const formatElapsed = (milliseconds: number) => {
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) return '0ms';
  if (milliseconds < 1000) return `${Math.round(milliseconds)}ms`;
  return `${(milliseconds / 1000).toFixed(1)}s`;
};

const getHealthStatus = (score: number = 100) => {
  if (score >= 90) return 'success';
  if (score >= 60) return 'warning';
  return 'exception';
};

const healthTagType = (status: string) => {
  if (status === 'healthy') return 'success';
  if (status === 'attention') return 'warning';
  return 'danger';
};

const healthStatusLabel = (status: string) => {
  if (status === 'healthy') return '正常';
  if (status === 'attention') return '关注';
  return '异常';
};

const severityTagType = (severity: string) => {
  if (severity === 'critical') return 'danger';
  if (severity === 'warning') return 'warning';
  return 'info';
};

const severityLabel = (severity: string) => {
  if (severity === 'critical') return '严重';
  if (severity === 'warning') return '注意';
  return '提示';
};

const usageSourceLabel = (source: string) => {
  if (source === 'treesize') return 'TreeSize';
  if (source === 'filesystem_scan') return '遍历';
  return source || '-';
};

// Loaders
const loadDashboard = async (includeWorkspace = true) => {
  dashboardLoading.value = true;
  try {
    dashboardStats.value = await fetchStorageDashboard();
    if (includeWorkspace) {
      await Promise.all([
        loadStorageUsage('data_workspace', false),
        loadStorageUsage('source_dir', false),
      ]);
    }
  } catch (error) {
    ElMessage.error('加载仪表盘失败');
  } finally {
    dashboardLoading.value = false;
  }
};

const assignUsage = (scope: StorageUsageScope, usage: WorkspaceUsageResponse) => {
  if (scope === 'source_dir') {
    sourceDirUsage.value = usage;
  } else {
    dataWorkspaceUsage.value = usage;
  }
};

const loadStorageUsage = async (scope: StorageUsageScope, refresh = false) => {
  usageLoadingByScope.value[scope] = true;
  try {
    assignUsage(scope, await fetchWorkspaceUsage(scope, refresh, 20));
  } catch (error) {
    ElMessage.error(`加载${scope === 'source_dir' ? '源码目录' : '数据工作区'}占用失败`);
  } finally {
    usageLoadingByScope.value[scope] = false;
  }
};

const loadActiveUsage = async (refresh = false) => {
  await loadStorageUsage(activeUsageScope.value, refresh);
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

const refreshData = async () => {
  loading.value = true;
  try {
    if (activeTab.value === 'dashboard') {
      await Promise.all([
        loadDashboard(false),
        loadStorageUsage('data_workspace', true),
        loadStorageUsage('source_dir', true),
      ]);
    }
    else if (activeTab.value === 'analysis') await loadAnalysis();
    else if (activeTab.value === 'maintenance') await loadMaintenance();
  } finally {
    loading.value = false;
  }
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

const openTreeSize = () => {
  router.push('/cluster/treesize');
};

const handleSlimmingAction = (candidate: StorageSlimmingCandidate) => {
  if (candidate.cleanup_kind === 'optimize_orphans' || candidate.category === 'attachments') {
    activeTab.value = 'maintenance';
    if (!maintenanceStatus.value) loadMaintenance();
    return;
  }
  openTreeSize();
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
  flex: 1 1 220px;
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

.metric-desc-lines {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}

.metric-subset {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.health-score {
  color: #67C23A;
}

.dashboard-content {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.workspace-usage {
  border: 1px solid #EBEEF5;
  border-radius: 4px;
  padding: 14px 16px;
  background: #fff;
}

.section-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.section-header h3 {
  margin: 0;
  font-size: 16px;
  color: #303133;
}

.usage-title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

.workspace-path {
  margin: 4px 0 0;
  max-width: 720px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #909399;
  font-size: 12px;
}

.workspace-role {
  margin: 3px 0 0;
  color: #606266;
  font-size: 12px;
}

.workspace-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 18px;
  margin-bottom: 12px;
  color: #606266;
  font-size: 13px;
}

.workspace-summary span {
  display: inline-flex;
  gap: 6px;
  align-items: center;
}

.workspace-summary strong {
  color: #303133;
}

.summary-warning {
  color: #E6A23C;
}

.governance-stack {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin-bottom: 14px;
}

.governance-section {
  min-width: 0;
}

.mini-section-title {
  margin-bottom: 8px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.health-table {
  width: max-content;
  max-width: 100%;
}

.empty-inline {
  padding: 10px 0;
  color: #909399;
  font-size: 13px;
}

.workspace-table {
  width: max-content;
  max-width: 100%;
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
