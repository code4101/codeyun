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
  type AttendanceConfigResponse,
  type AttendanceConfigUpdateRequest,
  type AttendanceOrderLookupMode,
  type AttendanceStepRunnerConfig,
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
const currentDataDeviceId = ref('')
const currentDataDeviceLabel = ref('')
const stepRunners = ref<AttendanceStepRunnerConfig[]>([])
const stepDeviceEntryIds = reactive<Record<string, string>>({
  '1': '',
  '2': '',
  '3': '',
  '4': '',
  '5': '',
  '6': '',
})
const scanReminderText = ref('')
const currentOrderLookupMode = ref<AttendanceOrderLookupMode>('browser_only')
const orderOperationPasswordInput = ref('')
const orderOperationPasswordConfigured = ref(false)
const clearOrderOperationPassword = ref(false)

const orderLookupModeOptions: Array<{ value: AttendanceOrderLookupMode; label: string }> = [
  { value: 'browser_only', label: '强制网页查最新数据' },
  { value: 'hybrid', label: '数据库优先，查不到再网页' },
  { value: 'db_only', label: '仅查看数据库缓存' },
]

const accountForm = reactive({
  login_username: '',
  password: '',
})

const account = computed(() => accounts.value[0] ?? null)

const fallbackStepRunners: AttendanceStepRunnerConfig[] = [
  { step: 1, title: 'step1 浏览器导入原始数据', default_role: 'execution_device', effective_role: 'execution_device' },
  { step: 2, title: 'step2 数据聚合与写回', default_role: 'data_host', effective_role: 'data_host' },
  { step: 3, title: 'step3 返款计算与高亮', default_role: 'data_host', effective_role: 'data_host' },
  { step: 4, title: 'step4 上午浏览器检查', default_role: 'execution_device', effective_role: 'execution_device' },
  { step: 5, title: 'step5 上午数据处理', default_role: 'data_host', effective_role: 'data_host' },
  { step: 6, title: 'step6 上午收尾执行', default_role: 'execution_device', effective_role: 'execution_device' },
]

const maskedPassword = computed(() => {
  const plain = account.value?.password || ''
  if (!plain) return '未配置'
  if (plain.length <= 2) return '*'.repeat(plain.length)
  return `${plain.slice(0, 1)}${'*'.repeat(Math.max(plain.length - 2, 4))}${plain.slice(-1)}`
})

const deviceOptions = computed(() => {
  const items = [...taskStore.devices]
  const ensureDeviceOption = (
    currentId: string,
    label: string,
    mode: 'local' | 'remote' = 'remote',
    serverUrl = '',
    deviceId = currentId,
  ) => {
    if (!currentId || items.some(item => item.id === currentId) || !label) return
    items.unshift({
      id: currentId,
      device_id: deviceId,
      name: label,
      mode,
      type: 'RemoteDevice',
      server_url: serverUrl,
    } as Device)
  }

  ensureDeviceOption(currentExecutionDeviceId.value, `${currentExecutionDeviceLabel.value}（当前执行设备）`)
  ensureDeviceOption(currentDataDeviceId.value, `${currentDataDeviceLabel.value}（数据主机）`)
  stepRunners.value.forEach((runner) => {
    const device = runner.device
    if (!device) return
    ensureDeviceOption(
      device.entry_id,
      `${device.name}（step${runner.step} 当前值）`,
      device.mode,
      device.server_url || '',
      device.device_id,
    )
  })
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

const selectedDataDeviceLabel = computed(() => {
  if (!currentDataDeviceId.value) return '当前 CodeYun 实例'
  const device = deviceOptions.value.find(item => item.id === currentDataDeviceId.value)
  return device ? getDeviceLabel(device) : (currentDataDeviceLabel.value || '未设置')
})

const stepRunnerRows = computed(() => stepRunners.value.length ? stepRunners.value : fallbackStepRunners)

const stepOverrideCount = computed(() => (
  Object.values(stepDeviceEntryIds).filter(Boolean).length
))

function parseLines(raw: string) {
  return raw
    .split(/\r?\n/g)
    .map(item => item.trim())
    .filter(Boolean)
}

function resetOrderOperationPasswordState(configured: boolean) {
  orderOperationPasswordInput.value = ''
  orderOperationPasswordConfigured.value = configured
  clearOrderOperationPassword.value = false
}

function handleOrderOperationPasswordInput() {
  clearOrderOperationPassword.value = false
}

function clearSavedOrderOperationPassword() {
  orderOperationPasswordInput.value = ''
  clearOrderOperationPassword.value = true
}

function setStepDeviceEntryIds(value?: Record<string, string | null>) {
  for (let step = 1; step <= 6; step += 1) {
    stepDeviceEntryIds[String(step)] = value?.[String(step)] || ''
  }
}

function applyConfig(config: AttendanceConfigResponse) {
  currentExecutionDeviceId.value = config.service.execution_device_entry_id || ''
  currentExecutionDeviceLabel.value = config.current_execution_device?.name || ''
  currentDataDeviceId.value = config.service.data_device_entry_id || ''
  currentDataDeviceLabel.value = config.current_data_device?.name || ''
  stepRunners.value = config.service.step_runners || []
  setStepDeviceEntryIds(config.service.step_device_entry_ids || {})
  scanReminderText.value = (config.service.scan_reminder_users || []).join('\n')
  currentOrderLookupMode.value = config.service.order_lookup_mode || 'browser_only'
  resetOrderOperationPasswordState(Boolean(config.service.order_operation_password_configured))
}

function getDefaultRunnerLabel(runner: AttendanceStepRunnerConfig) {
  return runner.default_role === 'execution_device' ? '默认执行设备' : '默认数据主机'
}

function getStepDefaultOptionLabel(runner: AttendanceStepRunnerConfig) {
  return runner.default_role === 'execution_device' ? '默认：当前执行设备' : '默认：数据主机'
}

function getEffectiveRunnerLabel(runner: AttendanceStepRunnerConfig) {
  if (runner.configured_device_entry_id) return '自定义设备'
  return getDefaultRunnerLabel(runner)
}

function getStepRunnerTitle(runner: AttendanceStepRunnerConfig) {
  return runner.title.replace(/^step\d+\s*/, '')
}

function buildStepDevicePayload() {
  const payload: Record<string, string> = {}
  for (let step = 1; step <= 6; step += 1) {
    payload[String(step)] = stepDeviceEntryIds[String(step)] || ''
  }
  return payload
}

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
    applyConfig(config)
  } catch (error: any) {
    ElMessage.error(error.response?.data?.detail || '加载考勤配置失败')
  } finally {
    loading.value = false
  }
}

async function saveCurrentSelections() {
  savingCurrent.value = true
  try {
    const payload: AttendanceConfigUpdateRequest = {
      execution_device_entry_id: currentExecutionDeviceId.value || '',
      data_device_entry_id: currentDataDeviceId.value || '',
      step_device_entry_ids: buildStepDevicePayload(),
      scan_reminder_users: parseLines(scanReminderText.value),
      order_lookup_mode: currentOrderLookupMode.value,
    }

    const trimmedOperationPassword = orderOperationPasswordInput.value.trim()
    if (clearOrderOperationPassword.value) {
      payload.clear_order_operation_password = true
    } else if (trimmedOperationPassword) {
      payload.order_operation_password = trimmedOperationPassword
    }

    const config = await updateAttendanceConfig(payload)
    applyConfig(config)
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
    ElMessage.error(error.response?.data?.detail || '保存问卷账号失败')
  } finally {
    savingAccount.value = false
  }
}

async function removeAccount(account: AttendanceAccount) {
  try {
    await ElMessageBox.confirm(`确定删除账号“${account.name}”吗？`, '删除问卷账号', {
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
    ElMessage.error(error.response?.data?.detail || '删除问卷账号失败')
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
        <p>这里维护问卷账号、默认执行设备、数据主机、六步运行位置、扫码提醒对象、操作密码和订单查单模式。</p>
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

          <el-form-item label="数据主机">
            <el-select
              v-model="currentDataDeviceId"
              filterable
              clearable
              placeholder="留空表示当前 CodeYun 实例"
            >
              <el-option label="当前 CodeYun 实例" value="" />
              <el-option
                v-for="device in deviceOptions"
                :key="device.id"
                :label="getDeviceLabel(device)"
                :value="device.id"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="订单扫码提醒对象（每行一个，可留空）">
            <el-input
              v-model="scanReminderText"
              type="textarea"
              :rows="4"
              placeholder="例如&#10;考勤后台&#10;文件传输助手"
            />
          </el-form-item>

          <el-form-item label="订单查单模式">
            <el-select v-model="currentOrderLookupMode">
              <el-option
                v-for="option in orderLookupModeOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="操作密码">
            <el-input
              v-model="orderOperationPasswordInput"
              type="password"
              show-password
              clearable
              placeholder="已配置时留空表示不修改；输入新值可覆盖"
              @input="handleOrderOperationPasswordInput"
            />
            <div class="field-meta-row">
              <el-tag :type="orderOperationPasswordConfigured && !clearOrderOperationPassword ? 'success' : 'info'" effect="plain">
                {{ clearOrderOperationPassword ? '本次保存后清空' : (orderOperationPasswordConfigured ? '已配置' : '未配置') }}
              </el-tag>
              <el-button
                v-if="orderOperationPasswordConfigured && !clearOrderOperationPassword"
                text
                type="danger"
                @click="clearSavedOrderOperationPassword"
              >
                清空已保存密码
              </el-button>
            </div>
          </el-form-item>

          <div class="step-runner-block">
            <div class="step-runner-heading">
              <span>六步运行位置</span>
              <el-tag size="small" effect="plain">{{ stepOverrideCount }} 个覆盖</el-tag>
            </div>
            <div class="step-runner-list">
              <div
                v-for="runner in stepRunnerRows"
                :key="runner.step"
                class="step-runner-row"
                :class="{ 'is-warning': runner.device_missing || runner.device_inactive }"
              >
                <div class="step-runner-main">
                  <strong>step{{ runner.step }}</strong>
                  <span>{{ getStepRunnerTitle(runner) }}</span>
                  <el-tag size="small" effect="plain">
                    {{ getEffectiveRunnerLabel(runner) }}
                  </el-tag>
                </div>
                <el-select
                  v-model="stepDeviceEntryIds[String(runner.step)]"
                  filterable
                  clearable
                  class="step-runner-select"
                  :placeholder="getStepDefaultOptionLabel(runner)"
                >
                  <el-option :label="getStepDefaultOptionLabel(runner)" value="" />
                  <el-option
                    v-for="device in deviceOptions"
                    :key="`${runner.step}-${device.id}`"
                    :label="getDeviceLabel(device)"
                    :value="device.id"
                  />
                </el-select>
              </div>
            </div>
          </div>

        </el-form>

        <div class="summary-strip">
          <span>问卷账号：{{ account?.login_username || '未配置' }}</span>
          <span>当前设备：{{ selectedExecutionDeviceLabel }}</span>
          <span>数据主机：{{ selectedDataDeviceLabel }}</span>
          <span>步骤覆盖：{{ stepOverrideCount ? `${stepOverrideCount} 个` : '使用默认规则' }}</span>
          <span>扫码提醒：{{ parseLines(scanReminderText).join('、') || '未配置' }}</span>
          <span>查单模式：{{ orderLookupModeOptions.find(item => item.value === currentOrderLookupMode)?.label || '强制网页查最新数据' }}</span>
          <span>操作密码：{{ clearOrderOperationPassword ? '本次保存后清空' : (orderOperationPasswordConfigured ? '已配置' : '未配置') }}</span>
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
            <h2>问卷账号</h2>
          </div>
          <el-button v-if="!account" type="primary" plain :icon="Plus" @click="openCreateAccountDialog">
            配置账号
          </el-button>
        </div>

        <div v-if="!account" class="placeholder-card">
          <p>还没有配置问卷账号。这里只支持一个全局账号；问卷课程清单会从星云表格自动读取。</p>
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
      :title="editingAccountId ? '编辑问卷账号' : '配置问卷账号'"
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
  gap: 16px;
}

.hero-panel {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  padding: 18px 20px;
  border-radius: 8px;
  background: #ffffff;
  color: #111827;
  border: 1px solid #e5e7eb;
}

.hero-copy h1 {
  margin: 4px 0 8px;
  font-size: 24px;
  line-height: 1.2;
}

.hero-copy p {
  margin: 0;
  max-width: 780px;
  line-height: 1.6;
  color: #4b5563;
}

.eyebrow {
  font-size: 12px;
  letter-spacing: 0;
  text-transform: uppercase;
  color: #64748b;
}

.attendance-grid {
  display: grid;
  grid-template-columns: minmax(560px, 1.35fr) minmax(320px, 0.65fr);
  gap: 16px;
}

.panel-card {
  padding: 20px;
  border-radius: 8px;
  background: #ffffff;
  border: 1px solid #e5e7eb;
}

.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.panel-kicker {
  margin: 0 0 4px;
  color: #64748b;
  font-size: 12px;
  letter-spacing: 0;
  text-transform: uppercase;
}

.panel-header h2 {
  margin: 0;
  color: #111827;
  font-size: 20px;
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
  padding: 12px 14px;
  border-radius: 8px;
  background: #f8fafc;
  color: #334155;
  border: 1px solid #e2e8f0;
}

.field-meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
}

.step-runner-block {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 4px;
}

.step-runner-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: #111827;
  font-size: 14px;
  font-weight: 600;
}

.step-runner-list {
  display: flex;
  flex-direction: column;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
}

.step-runner-row {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) minmax(240px, 0.8fr);
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  background: #ffffff;
  border-bottom: 1px solid #edf2f7;
}

.step-runner-row:last-child {
  border-bottom: 0;
}

.step-runner-row.is-warning {
  background: #fff7ed;
}

.step-runner-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.step-runner-main strong {
  color: #0f766e;
  font-size: 13px;
  white-space: nowrap;
}

.step-runner-main span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #374151;
}

.step-runner-select {
  width: 100%;
}

.action-row {
  margin-top: 18px;
  display: flex;
  gap: 12px;
}

.placeholder-card {
  padding: 16px;
  border-radius: 8px;
  background: #f8fafc;
  color: #475569;
  border: 1px solid #e2e8f0;
}

.single-account-card {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px;
  border-radius: 8px;
  background: #f8fafc;
  color: #1f2937;
  border: 1px solid #e2e8f0;
}

.account-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.account-label {
  color: #64748b;
}

.account-value {
  font-weight: 600;
  color: #111827;
}

.masked-password {
  padding: 4px 10px;
  border-radius: 6px;
  background: #ffffff;
  color: #334155;
  border: 1px solid #e5e7eb;
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

  .step-runner-row {
    grid-template-columns: 1fr;
  }
}
</style>
