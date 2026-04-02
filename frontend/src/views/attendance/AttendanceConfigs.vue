<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Delete, Edit, Plus, RefreshRight } from '@element-plus/icons-vue'

import {
  createAttendanceAccount,
  deleteAttendanceAccount,
  fetchAttendanceAccounts,
  fetchAttendanceConfig,
  type AttendanceAccount,
  updateAttendanceAccount,
  updateAttendanceConfig,
} from '@/api/attendance'
import { taskStore, type Device } from '@/store/taskStore'

const loading = ref(false)
const savingCurrent = ref(false)
const savingAccount = ref(false)
const accountDialogVisible = ref(false)
const editingAccountId = ref('')
const accounts = ref<AttendanceAccount[]>([])
const currentExecutionDeviceId = ref('')
const currentExecutionDeviceLabel = ref('')

const accountForm = reactive({
  login_username: '',
  password: '',
})

const account = computed(() => accounts.value[0] ?? null)

const maskedPassword = computed(() => {
  const plain = account.value?.password || ''
  if (!plain) return '未配置'
  if (plain.length <= 2) return '*'.repeat(plain.length)
  return `${plain.slice(0, 1)}${'*'.repeat(Math.max(plain.length - 2, 4))}${plain.slice(-1)}`
})

const deviceOptions = computed(() => {
  const items = [...taskStore.devices]
  const currentId = currentExecutionDeviceId.value
  if (currentId && !items.some(item => item.id === currentId) && currentExecutionDeviceLabel.value) {
    items.unshift({
      id: currentId,
      device_id: currentId,
      name: `${currentExecutionDeviceLabel.value}（当前全局值）`,
      mode: 'remote',
      type: 'RemoteDevice',
      server_url: '',
    } as Device)
  }
  return items
})

function getDeviceLabel(device: Device) {
  return `${device.name} · ${device.mode === 'local' ? '本地设备' : '远程设备'}`
}

const selectedExecutionDeviceLabel = computed(() => {
  if (!currentExecutionDeviceId.value) return '未设置'
  const device = deviceOptions.value.find(item => item.id === currentExecutionDeviceId.value)
  return device ? getDeviceLabel(device) : (currentExecutionDeviceLabel.value || '未设置')
})

function resetAccountForm() {
  accountForm.login_username = ''
  accountForm.password = ''
  editingAccountId.value = ''
}

function openCreateAccountDialog() {
  resetAccountForm()
  accountDialogVisible.value = true
}

function openEditAccountDialog(account: AttendanceAccount) {
  editingAccountId.value = account.id
  accountForm.login_username = account.login_username
  accountForm.password = account.password || ''
  accountDialogVisible.value = true
}

async function loadPageData() {
  loading.value = true
  try {
    const [config, accountItems] = await Promise.all([
      fetchAttendanceConfig(),
      fetchAttendanceAccounts(),
      taskStore.fetchDevices(),
    ])
    accounts.value = accountItems
    currentExecutionDeviceId.value = config.service.execution_device_entry_id || ''
    currentExecutionDeviceLabel.value = config.current_execution_device?.name || ''
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '加载考勤配置失败')
  } finally {
    loading.value = false
  }
}

async function saveCurrentSelections() {
  savingCurrent.value = true
  try {
    const config = await updateAttendanceConfig({
      execution_device_entry_id: currentExecutionDeviceId.value || '',
    })
    currentExecutionDeviceId.value = config.service.execution_device_entry_id || ''
    currentExecutionDeviceLabel.value = config.current_execution_device?.name || ''
    ElMessage.success('全局当前配置已保存')
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '保存全局当前配置失败')
  } finally {
    savingCurrent.value = false
  }
}

async function submitAccount() {
  const payload = {
    login_username: accountForm.login_username.trim(),
    password: accountForm.password,
  }
  if (!payload.login_username || !payload.password) {
    ElMessage.warning('请先填写登录账号和密码')
    return
  }

  savingAccount.value = true
  try {
    if (editingAccountId.value) {
      await updateAttendanceAccount(editingAccountId.value, payload)
    } else {
      await createAttendanceAccount(payload)
    }
    accountDialogVisible.value = false
    resetAccountForm()
    await loadPageData()
    ElMessage.success(editingAccountId.value ? '账号已更新' : '账号已创建')
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '保存问卷星账号失败')
  } finally {
    savingAccount.value = false
  }
}

async function removeAccount(account: AttendanceAccount) {
  try {
    await ElMessageBox.confirm(`确定删除账号“${account.name}”吗？`, '删除问卷星账号', {
      type: 'warning',
    })
  } catch {
    return
  }

  try {
    await deleteAttendanceAccount(account.id)
    await loadPageData()
    ElMessage.success('账号已删除')
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '删除问卷星账号失败')
  }
}

onMounted(() => {
  void loadPageData()
})
</script>

<template>
  <div class="attendance-page">
    <section class="hero-panel">
      <div class="hero-copy">
        <div class="eyebrow">禅寺考勤 / 考勤配置</div>
        <h1>考勤配置</h1>
        <p>这里维护唯一问卷星账号与全局唯一执行设备。问卷星模版页只读取这些默认值，不再另起一套配置源。</p>
      </div>
      <el-button type="primary" :icon="RefreshRight" :loading="loading" @click="loadPageData">
        刷新
      </el-button>
    </section>

    <div class="attendance-grid">
      <section class="panel-card">
        <div class="panel-header">
          <div>
            <p class="panel-kicker">全局当前值</p>
            <h2>默认执行配置</h2>
          </div>
          <el-tag type="warning" effect="plain">管理员共享</el-tag>
        </div>

        <el-form label-position="top" class="settings-form">
          <el-form-item label="当前执行设备">
            <el-select
              v-model="currentExecutionDeviceId"
              filterable
              clearable
              placeholder="从你已有的设备资产里选一个"
            >
              <el-option
                v-for="device in deviceOptions"
                :key="device.id"
                :label="getDeviceLabel(device)"
                :value="device.id"
              />
            </el-select>
          </el-form-item>
        </el-form>

        <div class="summary-strip">
          <span>问卷星账号：{{ account?.login_username || '未配置' }}</span>
          <span>当前设备：{{ selectedExecutionDeviceLabel }}</span>
        </div>

        <div class="action-row">
          <el-button type="primary" :icon="Check" :loading="savingCurrent" @click="saveCurrentSelections">
            保存全局当前配置
          </el-button>
        </div>
      </section>

      <section class="panel-card">
        <div class="panel-header">
          <div>
            <p class="panel-kicker">单账号</p>
            <h2>问卷星账号</h2>
          </div>
          <el-button v-if="!account" type="primary" plain :icon="Plus" @click="openCreateAccountDialog">
            配置账号
          </el-button>
        </div>

        <div v-if="!account" class="placeholder-card">
          <p>还没有配置问卷星账号。这里只支持一个全局账号，配置完成后问卷星模版页会直接复用。</p>
        </div>

        <div v-else class="single-account-card">
          <div class="account-row">
            <span class="account-label">登录账号</span>
            <strong class="account-value">{{ account.login_username }}</strong>
          </div>
          <div class="account-row">
            <span class="account-label">登录密码</span>
            <code class="masked-password">{{ maskedPassword }}</code>
          </div>
          <div class="action-row compact">
            <el-button text type="primary" :icon="Edit" @click="openEditAccountDialog(account)">编辑账号</el-button>
            <el-button text type="danger" :icon="Delete" @click="removeAccount(account)">删除账号</el-button>
          </div>
        </div>
      </section>
    </div>

    <el-dialog
      v-model="accountDialogVisible"
      :title="editingAccountId ? '编辑问卷星账号' : '配置问卷星账号'"
      width="520px"
      destroy-on-close
    >
      <el-form label-position="top">
        <el-form-item label="登录账号">
          <el-input v-model="accountForm.login_username" placeholder="手机号或用户名" />
        </el-form-item>
        <el-form-item label="登录密码">
          <el-input v-model="accountForm.password" type="textarea" :rows="3" placeholder="外层默认掩码显示，只有在编辑态才回显明文" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="accountDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingAccount" @click="submitAccount">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.attendance-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.hero-panel {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  padding: 28px 30px;
  border-radius: 24px;
  background:
    linear-gradient(135deg, rgba(23, 82, 72, 0.95), rgba(225, 175, 109, 0.92)),
    linear-gradient(160deg, #12352f, #b9833b);
  color: #fff8eb;
  box-shadow: 0 18px 40px rgba(20, 46, 42, 0.18);
}

.hero-copy h1 {
  margin: 6px 0 10px;
  font-size: 30px;
  line-height: 1.1;
}

.hero-copy p {
  margin: 0;
  max-width: 720px;
  line-height: 1.7;
  color: rgba(255, 248, 235, 0.92);
}

.eyebrow {
  font-size: 12px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: rgba(255, 248, 235, 0.72);
}

.attendance-grid {
  display: grid;
  grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
  gap: 20px;
}

.panel-card {
  padding: 24px;
  border-radius: 22px;
  background: #fffaf1;
  border: 1px solid rgba(142, 109, 57, 0.14);
  box-shadow: 0 12px 30px rgba(81, 57, 26, 0.08);
}

.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.panel-kicker {
  margin: 0 0 4px;
  color: #8a6a3f;
  font-size: 12px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.panel-header h2 {
  margin: 0;
  color: #2d2417;
  font-size: 22px;
}

.settings-form {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.summary-strip {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 16px 0 0;
  padding: 14px 16px;
  border-radius: 16px;
  background: rgba(219, 194, 146, 0.18);
  color: #5b4427;
}

.action-row {
  margin-top: 18px;
  display: flex;
  gap: 12px;
}

.placeholder-card {
  padding: 18px;
  border-radius: 16px;
  background: rgba(219, 194, 146, 0.14);
  color: #6b5332;
}

.single-account-card {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 18px;
  border-radius: 18px;
  background: rgba(219, 194, 146, 0.16);
  color: #3d301f;
}

.account-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.account-label {
  color: #8a6a3f;
}

.account-value {
  font-weight: 600;
  color: #2d2417;
}

.masked-password {
  padding: 4px 10px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.72);
  color: #5f4526;
}

.action-row.compact {
  margin-top: 4px;
}

@media (max-width: 960px) {
  .hero-panel {
    flex-direction: column;
  }

  .attendance-grid {
    grid-template-columns: 1fr;
  }
}
</style>
