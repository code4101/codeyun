<template>
  <div class="ai-wechat-page">
    <section class="hero-panel">
      <div class="hero-copy">
        <h1>CodeClaw 微信接入</h1>
      </div>
    </section>

    <div class="workspace-grid">
      <aside class="control-panel">
        <section class="panel-card">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">连接</p>
              <h2>扫码授权</h2>
            </div>
            <div class="panel-header-actions">
              <el-popover placement="bottom-end" width="280" trigger="hover">
                <p class="help-popover-text">
                  这里直连腾讯 iLink 的个人微信私聊通道。当前只做扫码、收私聊消息和按上下文回复，不做群控、朋友圈或公众号运营自动化。
                </p>
                <template #reference>
                  <el-button text :icon="QuestionFilled" title="接入说明" aria-label="接入说明" />
                </template>
              </el-popover>
              <el-button text :icon="RefreshRight" :loading="statusLoading" @click="loadStatus">
                刷新
              </el-button>
            </div>
          </div>

          <div class="runtime-row">
            <span>iLink {{ runtime.channelVersion || '-' }}</span>
            <span>{{ runtime.baseUrl || '-' }}</span>
          </div>

          <div v-if="qrcodeUrl" class="qr-panel">
            <img :src="qrcodeUrl" alt="微信登录二维码" class="qr-image">
            <div class="login-status-row">
              <el-tag :type="getLoginStatusTagType(loginStatus)" effect="plain">
                {{ getLoginStatusLabel(loginStatus) }}
              </el-tag>
              <span>{{ loginMessage }}</span>
            </div>
          </div>
          <div v-else class="placeholder-block">
            <p>生成二维码后，用手机微信扫码并确认授权。</p>
          </div>

          <div class="action-row">
            <el-button
              type="primary"
              :icon="Connection"
              :loading="startingLogin"
              @click="startLoginFlow"
            >
              {{ qrcodeUrl ? '重新生成二维码' : '生成二维码' }}
            </el-button>
            <el-button
              v-if="loginSessionKey"
              text
              :loading="loginPolling"
              :disabled="loginPolling"
              @click="pollLoginOnce"
            >
              检查登录
            </el-button>
          </div>
        </section>

        <section class="panel-card">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">账号</p>
              <h2>已连接微信</h2>
            </div>
          </div>

          <div v-if="!accounts.length" class="placeholder-block">
            <p>还没有已保存的微信连接。</p>
          </div>

          <div v-else class="account-list">
            <button
              v-for="account in accounts"
              :key="account.account_id"
              type="button"
              class="account-item"
              :class="{ active: account.account_id === selectedAccountId }"
              @click="selectAccount(account.account_id)"
            >
              <div class="account-main">
                <strong>{{ account.user_id || account.account_id }}</strong>
                <span>{{ account.account_id }}</span>
              </div>
              <div class="account-meta">
                <el-tag v-if="account.has_cursor" size="small" effect="plain" type="success">
                  已同步
                </el-tag>
                <span>{{ formatTime(account.updated_at) }}</span>
                <el-button
                  class="account-delete-button"
                  text
                  size="small"
                  type="danger"
                  :icon="Delete"
                  title="删除连接"
                  aria-label="删除连接"
                  @click.stop="confirmDeleteAccount(account)"
                />
              </div>
            </button>
          </div>

          <div v-if="selectedAccount" class="bridge-section">
            <div class="bridge-section-head">
              <div>
                <strong>Codex 自动回复</strong>
                <span>{{ getBridgeStateText(selectedAccount) }}</span>
              </div>
              <el-tag
                size="small"
                effect="plain"
                :type="selectedAccount.codex_bridge?.running ? 'success' : 'info'"
              >
                {{ selectedAccount.codex_bridge?.running ? '运行中' : '未运行' }}
              </el-tag>
            </div>
            <div class="bridge-meta">
              <span>模型 {{ selectedAccount.codex_bridge?.model || DEFAULT_CODEX_MODEL }}</span>
              <span>已处理 {{ selectedAccount.codex_bridge?.handled_count || 0 }} 条</span>
            </div>
            <p v-if="selectedAccount.codex_bridge?.last_error" class="bridge-error">
              {{ selectedAccount.codex_bridge.last_error }}
            </p>
            <div class="action-row">
              <el-button
                v-if="!selectedAccount.codex_bridge?.running"
                type="primary"
                plain
                :loading="bridgeStarting"
                @click="startCodexBridge"
              >
                开启
              </el-button>
              <el-button
                v-else
                type="danger"
                plain
                :loading="bridgeStopping"
                @click="stopCodexBridge"
              >
                停止
              </el-button>
            </div>
          </div>
        </section>
      </aside>

      <section class="main-panel">
        <section class="panel-card message-panel">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">私聊</p>
              <h2>{{ selectedAccount ? '消息' : '未选择账号' }}</h2>
            </div>
            <div class="panel-header-actions">
              <el-button
                text
                :icon="RefreshRight"
                :loading="updatesLoading"
                :disabled="!selectedAccount || isBridgeRunning"
                @click="pullUpdates"
              >
                {{ isBridgeRunning ? '自动监听中' : (updatesLoading ? '等待新消息' : '拉取消息') }}
              </el-button>
            </div>
          </div>

          <div v-if="!selectedAccount" class="placeholder-block">
            <p>左侧选择一个微信连接，再拉取最近消息。</p>
          </div>

          <div v-else-if="!messages.length" class="placeholder-block">
            <p>这个接口只接收发给 ClawBot 的新消息，不会读取普通微信聊天记录或历史消息。</p>
            <p>在手机微信里打开 ClawBot 入口，给它发一条新消息，再点“拉取消息”。按钮转圈时是在等待新消息，最长约 35 秒。</p>
            <p v-if="lastPullMessage" class="pull-result-text">{{ lastPullMessage }}</p>
          </div>

          <div v-else class="message-list">
            <button
              v-for="(message, index) in messages"
              :key="getMessageKey(message)"
              type="button"
              class="message-item"
              :class="{ active: index === selectedMessageIndex }"
              @click="selectMessage(index)"
            >
              <div class="message-head">
                <span class="message-user">{{ message.from_user_id || '未知发送方' }}</span>
                <span>{{ formatTime(message.create_time_ms) }}</span>
              </div>
              <p class="message-text">{{ message.text || getMessageTypeLabel(message) }}</p>
              <div v-if="message.images?.length" class="message-image-list">
                <figure v-for="image in message.images" :key="image.id" class="message-image">
                  <img v-if="getImageSrc(image)" :src="getImageSrc(image)" alt="微信图片">
                  <figcaption v-else>{{ image.download_error || '图片暂不可预览' }}</figcaption>
                </figure>
              </div>
              <div class="message-meta">
                <span>type {{ message.message_type ?? '-' }}</span>
                <el-tag v-if="message.context_token" size="small" effect="plain">
                  可回复
                </el-tag>
              </div>
            </button>
          </div>
        </section>

        <section class="panel-card reply-panel">
          <div class="panel-header">
            <div>
              <p class="panel-kicker">回复</p>
              <h2>{{ replyTarget || '选择消息后回复' }}</h2>
            </div>
            <el-tag v-if="contextTokenDraft" effect="plain" type="success">
              上下文
            </el-tag>
          </div>

          <el-form label-position="top" class="reply-form">
            <el-form-item label="接收方">
              <el-input
                v-model="replyTarget"
                clearable
                placeholder="选择左侧消息后自动填入，也可以手动填写 from_user_id"
              />
            </el-form-item>
            <el-form-item label="内容">
              <el-input
                v-model="replyText"
                type="textarea"
                :rows="5"
                maxlength="2000"
                show-word-limit
                placeholder="输入要发送给对方的文字；也可以只发送图片"
              />
            </el-form-item>
            <el-form-item label="图片">
              <div class="reply-image-row">
                <el-button :icon="Picture" @click="triggerImagePicker">
                  {{ replyImageFile ? '更换图片' : '选择图片' }}
                </el-button>
                <span v-if="replyImageFile">{{ replyImageFile.name }}</span>
                <el-button
                  v-if="replyImageFile"
                  text
                  type="danger"
                  :icon="Delete"
                  title="移除图片"
                  aria-label="移除图片"
                  @click="clearReplyImage"
                />
              </div>
              <img v-if="replyImagePreviewUrl" :src="replyImagePreviewUrl" alt="待发送图片" class="reply-image-preview">
              <input
                ref="imageInputRef"
                class="hidden-file-input"
                type="file"
                accept="image/*"
                @change="handleImagePicked"
              >
            </el-form-item>
          </el-form>

          <div class="action-row">
            <el-button
              type="primary"
              :icon="ChatDotRound"
              :loading="sending"
              :disabled="!canSendReply"
              @click="sendReply"
            >
              发送
            </el-button>
            <el-button text :disabled="!selectedMessage" @click="copyContextToken">
              复制上下文 token
            </el-button>
          </div>
        </section>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ChatDotRound,
  Connection,
  Delete,
  Picture,
  QuestionFilled,
  RefreshRight,
} from '@element-plus/icons-vue'

import {
  deleteWechatIlinkAccount,
  fetchWechatIlinkStatus,
  pullWechatIlinkUpdates,
  sendWechatIlinkImage,
  sendWechatIlinkText,
  startWechatIlinkCodexBridge,
  startWechatIlinkLogin,
  stopWechatIlinkCodexBridge,
  waitWechatIlinkLogin,
  type WechatIlinkAccountSummary,
  type WechatIlinkImageSummary,
  type WechatIlinkMessageSummary,
} from '@/api/wechatIlink'

const LOGIN_POLL_TIMEOUT_MS = 35_000
const DEFAULT_CODEX_MODEL = 'gpt-5.5'

const runtime = reactive({
  baseUrl: '',
  channelVersion: '',
})
const accounts = ref<WechatIlinkAccountSummary[]>([])
const selectedAccountId = ref('')
const statusLoading = ref(false)

const startingLogin = ref(false)
const loginPolling = ref(false)
const loginSessionKey = ref('')
const qrcodeUrl = ref('')
const loginStatus = ref('idle')
const loginMessage = ref('')
let loginPollStopped = false

const updatesLoading = ref(false)
const messages = ref<WechatIlinkMessageSummary[]>([])
const selectedMessageIndex = ref<number | null>(null)
const replyTarget = ref('')
const contextTokenDraft = ref('')
const replyText = ref('')
const replyImageFile = ref<File | null>(null)
const replyImagePreviewUrl = ref('')
const imageInputRef = ref<HTMLInputElement | null>(null)
const sending = ref(false)
const lastPullMessage = ref('')
const bridgeStarting = ref(false)
const bridgeStopping = ref(false)

const selectedAccount = computed(() =>
  accounts.value.find(account => account.account_id === selectedAccountId.value) ?? null,
)

const selectedMessage = computed(() => {
  if (selectedMessageIndex.value == null) return null
  return messages.value[selectedMessageIndex.value] ?? null
})

const isBridgeRunning = computed(() => Boolean(selectedAccount.value?.codex_bridge?.running))

const canSendReply = computed(() =>
  Boolean(selectedAccount.value && replyTarget.value.trim() && (replyText.value.trim() || replyImageFile.value)),
)

onMounted(() => {
  void loadStatus()
})

onBeforeUnmount(() => {
  loginPollStopped = true
  clearReplyImage()
})

async function loadStatus() {
  statusLoading.value = true
  try {
    const payload = await fetchWechatIlinkStatus()
    runtime.baseUrl = payload.base_url
    runtime.channelVersion = payload.channel_version
    accounts.value = payload.accounts
    if (!accounts.value.some(account => account.account_id === selectedAccountId.value)) {
      selectedAccountId.value = accounts.value[0]?.account_id ?? ''
      clearMessages()
    }
  } catch (error) {
    ElMessage.error(getErrorMessage(error, '微信接入状态加载失败'))
  } finally {
    statusLoading.value = false
  }
}

function selectAccount(accountId: string) {
  if (selectedAccountId.value === accountId) return
  selectedAccountId.value = accountId
  clearMessages()
}

function clearMessages() {
  messages.value = []
  selectedMessageIndex.value = null
  replyTarget.value = ''
  contextTokenDraft.value = ''
  replyText.value = ''
  clearReplyImage()
  lastPullMessage.value = ''
}

async function startLoginFlow() {
  startingLogin.value = true
  loginPollStopped = false
  try {
    const payload = await startWechatIlinkLogin({ force: true })
    loginSessionKey.value = payload.session_key
    qrcodeUrl.value = payload.qrcode_url
    loginStatus.value = payload.status
    loginMessage.value = payload.message
    void runLoginPollLoop()
  } catch (error) {
    ElMessage.error(getErrorMessage(error, '二维码生成失败'))
  } finally {
    startingLogin.value = false
  }
}

async function runLoginPollLoop() {
  if (loginPolling.value) return
  loginPolling.value = true
  try {
    while (loginSessionKey.value && !loginPollStopped) {
      const done = await pollLoginOnce()
      if (done) break
      await sleep(loginStatus.value === 'scaned' ? 1500 : 300)
    }
  } finally {
    loginPolling.value = false
  }
}

async function pollLoginOnce(): Promise<boolean> {
  if (!loginSessionKey.value) return true
  try {
    const payload = await waitWechatIlinkLogin({
      session_key: loginSessionKey.value,
      timeout_ms: LOGIN_POLL_TIMEOUT_MS,
    })
    loginStatus.value = payload.status
    loginMessage.value = payload.message
    if (payload.connected) {
      ElMessage.success('微信已连接')
      loginSessionKey.value = ''
      qrcodeUrl.value = ''
      await loadStatus()
      if (payload.account?.account_id) {
        selectedAccountId.value = payload.account.account_id
      }
      return true
    }
    if (payload.status === 'expired' || payload.status === 'missing') {
      loginSessionKey.value = ''
      return true
    }
  } catch (error) {
    ElMessage.error(getErrorMessage(error, '登录状态检查失败'))
    loginSessionKey.value = ''
    return true
  }
  return false
}

async function confirmDeleteAccount(account: WechatIlinkAccountSummary) {
  try {
    await ElMessageBox.confirm(
      `删除 ${account.user_id || account.account_id} 的微信连接？`,
      '删除连接',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }

  try {
    await deleteWechatIlinkAccount(account.account_id)
    ElMessage.success('已删除微信连接')
    await loadStatus()
  } catch (error) {
    ElMessage.error(getErrorMessage(error, '删除连接失败'))
  }
}

function replaceAccountSummary(account: WechatIlinkAccountSummary) {
  accounts.value = accounts.value.map(item => (
    item.account_id === account.account_id ? account : item
  ))
}

async function startCodexBridge() {
  if (!selectedAccount.value) return
  bridgeStarting.value = true
  try {
    const response = await startWechatIlinkCodexBridge(selectedAccount.value.account_id)
    replaceAccountSummary(response.account)
    ElMessage.success('Codex 自动回复已开启')
  } catch (error) {
    ElMessage.error(getErrorMessage(error, '开启 Codex 自动回复失败'))
  } finally {
    bridgeStarting.value = false
  }
}

async function stopCodexBridge() {
  if (!selectedAccount.value) return
  bridgeStopping.value = true
  try {
    const response = await stopWechatIlinkCodexBridge(selectedAccount.value.account_id)
    replaceAccountSummary(response.account)
    ElMessage.success('Codex 自动回复已停止')
  } catch (error) {
    ElMessage.error(getErrorMessage(error, '停止 Codex 自动回复失败'))
  } finally {
    bridgeStopping.value = false
  }
}

async function pullUpdates() {
  if (!selectedAccount.value) return
  if (isBridgeRunning.value) {
    ElMessage.info('CodeClaw 自动监听中，不需要手动拉取消息')
    return
  }
  updatesLoading.value = true
  try {
    lastPullMessage.value = ''
    const payload = await pullWechatIlinkUpdates(selectedAccount.value.account_id)
    if (payload.errmsg) {
      ElMessage.warning(payload.errmsg)
    }
    const incoming = payload.messages.filter(message => !messages.value.some(
      existing => getMessageKey(existing) === getMessageKey(message),
    ))
    messages.value = [...messages.value, ...incoming]
    if (incoming.length && selectedMessageIndex.value == null) {
      selectMessage(messages.value.length - incoming.length)
    }
    if (!incoming.length) {
      lastPullMessage.value = payload.timed_out
        ? '这次等待期间没有收到新消息。请确认手机微信里已经给 ClawBot 发了消息。'
        : '这次没有返回新消息。'
      ElMessage.info(payload.timed_out ? '这次等待期间没有新消息' : '没有新消息')
    } else {
      lastPullMessage.value = `收到 ${incoming.length} 条新消息。`
    }
    await loadStatus()
  } catch (error) {
    ElMessage.error(getErrorMessage(error, '消息拉取失败'))
  } finally {
    updatesLoading.value = false
  }
}

function selectMessage(index: number) {
  const message = messages.value[index]
  if (!message) return
  selectedMessageIndex.value = index
  replyTarget.value = message.from_user_id
  contextTokenDraft.value = message.context_token
}

async function sendReply() {
  if (!selectedAccount.value || !canSendReply.value) return
  sending.value = true
  try {
    const outgoingText = replyText.value.trim()
    const outgoingImage = replyImageFile.value
    const outgoingImagePreview = replyImagePreviewUrl.value
    const sent = outgoingImage
      ? await sendWechatIlinkImage(selectedAccount.value.account_id, {
        to_user_id: replyTarget.value.trim(),
        text: outgoingText,
        image: outgoingImage,
        context_token: contextTokenDraft.value || null,
      })
      : await sendWechatIlinkText(selectedAccount.value.account_id, {
        to_user_id: replyTarget.value.trim(),
        text: outgoingText,
        context_token: contextTokenDraft.value || null,
      })
    const sentImages: WechatIlinkImageSummary[] = outgoingImage
      ? [{
        ...sent.image,
        id: sent.image?.id || sent.message_id,
        mime_type: sent.image?.mime_type || outgoingImage.type || 'image/*',
        size: sent.image?.size || outgoingImage.size,
        preview_url: outgoingImagePreview,
      }]
      : []
    messages.value = [
      ...messages.value,
      {
        message_id: sent.message_id,
        from_user_id: selectedAccount.value.user_id || selectedAccount.value.account_id,
        to_user_id: sent.to_user_id,
        create_time_ms: Date.now(),
        session_id: '',
        message_type: 2,
        message_state: 2,
        context_token: contextTokenDraft.value,
        text: outgoingText,
        images: sentImages,
        item_types: outgoingImage ? (outgoingText ? [1, 2] : [2]) : [1],
        raw: {},
      },
    ]
    replyText.value = ''
    if (outgoingImage) {
      replyImageFile.value = null
      replyImagePreviewUrl.value = ''
      if (imageInputRef.value) imageInputRef.value.value = ''
    }
    ElMessage.success('已发送')
    await loadStatus()
  } catch (error) {
    ElMessage.error(getErrorMessage(error, '发送失败'))
  } finally {
    sending.value = false
  }
}

function triggerImagePicker() {
  imageInputRef.value?.click()
}

function handleImagePicked(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  if (!file.type.startsWith('image/')) {
    ElMessage.warning('只能选择图片文件')
    input.value = ''
    return
  }
  clearReplyImage()
  replyImageFile.value = file
  replyImagePreviewUrl.value = URL.createObjectURL(file)
}

function clearReplyImage() {
  if (replyImagePreviewUrl.value?.startsWith('blob:')) {
    URL.revokeObjectURL(replyImagePreviewUrl.value)
  }
  replyImageFile.value = null
  replyImagePreviewUrl.value = ''
  if (imageInputRef.value) imageInputRef.value.value = ''
}

async function copyContextToken() {
  if (!contextTokenDraft.value) {
    ElMessage.info('当前消息没有上下文 token')
    return
  }
  try {
    await navigator.clipboard.writeText(contextTokenDraft.value)
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败')
  }
}

function getMessageKey(message: WechatIlinkMessageSummary) {
  return String(message.message_id ?? message.seq ?? `${message.from_user_id}-${message.create_time_ms ?? ''}-${message.text}`)
}

function getMessageTypeLabel(message: WechatIlinkMessageSummary) {
  if (!message.item_types.length) return '非文本消息'
  return `非文本消息：${message.item_types.join(', ')}`
}

function getImageSrc(image: WechatIlinkImageSummary) {
  return image.data_url || image.preview_url || ''
}

function getLoginStatusLabel(status: string) {
  const labels: Record<string, string> = {
    idle: '未开始',
    wait: '等待扫码',
    scaned: '已扫码',
    scaned_but_redirect: '跳转中',
    confirmed: '已连接',
    expired: '已过期',
    missing: '已结束',
  }
  return labels[status] ?? status
}

function getLoginStatusTagType(status: string) {
  if (status === 'confirmed') return 'success'
  if (status === 'scaned' || status === 'scaned_but_redirect') return 'warning'
  if (status === 'expired' || status === 'missing') return 'danger'
  return 'info'
}

function getBridgeStateText(account: WechatIlinkAccountSummary) {
  const bridge = account.codex_bridge || {}
  if (bridge.running) {
    return bridge.last_reply_at ? `最近回复 ${formatTime(bridge.last_reply_at)}` : '收到新消息后会交给 Codex CLI'
  }
  if (bridge.enabled) {
    return '已启用，但当前后台监听未运行'
  }
  return '开启后，新消息会自动交给 Codex CLI 回复'
}

function formatTime(value: number | string | null | undefined) {
  if (value == null || value === '') return '-'
  const numeric = Number(value)
  if (!Number.isFinite(numeric) || numeric <= 0) return '-'
  const millis = numeric > 10_000_000_000 ? numeric : numeric * 1000
  return new Date(millis).toLocaleString()
}

function getErrorMessage(error: unknown, fallback: string) {
  if (typeof error === 'object' && error !== null && 'response' in error) {
    const response = (error as { response?: { data?: { detail?: string } } }).response
    const detail = response?.data?.detail
    if (detail) return detail
  }
  if (error instanceof Error && error.message) return error.message
  return fallback
}

function sleep(ms: number) {
  return new Promise(resolve => window.setTimeout(resolve, ms))
}
</script>

<style scoped>
.ai-wechat-page {
  min-height: 100%;
  padding: 24px;
  background: #f6f7f9;
  color: #1f2933;
}

.hero-panel {
  margin-bottom: 18px;
}

.hero-copy h1 {
  margin: 0;
  font-size: 28px;
  line-height: 1.25;
  font-weight: 700;
  letter-spacing: 0;
}

.workspace-grid {
  display: grid;
  grid-template-columns: minmax(300px, 360px) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.control-panel,
.main-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.panel-card {
  background: #fff;
  border: 1px solid #d9e0e8;
  border-radius: 8px;
  padding: 16px;
}

.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.panel-header h2 {
  margin: 2px 0 0;
  font-size: 18px;
  line-height: 1.35;
  font-weight: 650;
  letter-spacing: 0;
}

.panel-kicker {
  margin: 0;
  font-size: 12px;
  line-height: 1.4;
  color: #6b7280;
}

.panel-header-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.help-popover-text {
  margin: 0;
  color: #4b5563;
  line-height: 1.65;
}

.runtime-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  color: #6b7280;
  font-size: 12px;
}

.qr-panel {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 12px 0;
}

.qr-image {
  width: min(220px, 100%);
  aspect-ratio: 1;
  object-fit: contain;
  border: 1px solid #d9e0e8;
  border-radius: 8px;
  background: #fff;
}

.login-status-row,
.action-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.login-status-row {
  justify-content: center;
  color: #4b5563;
  font-size: 13px;
}

.placeholder-block {
  padding: 18px 0;
  color: #6b7280;
}

.placeholder-block p {
  margin: 0;
  line-height: 1.6;
}

.placeholder-block p + p {
  margin-top: 6px;
}

.pull-result-text {
  color: #409eff;
}

.account-list,
.message-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.account-item,
.message-item {
  width: 100%;
  text-align: left;
  border: 1px solid #d9e0e8;
  border-radius: 8px;
  background: #fff;
  color: inherit;
  cursor: pointer;
  transition: border-color 0.16s ease, background-color 0.16s ease;
}

.account-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 8px;
  padding: 10px 12px;
}

.account-item:hover,
.message-item:hover,
.account-item.active,
.message-item.active {
  border-color: #409eff;
  background: #f0f7ff;
}

.account-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.account-main strong,
.message-user {
  color: #111827;
  font-weight: 650;
}

.account-main strong,
.account-main span {
  display: block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.account-main span,
.account-meta,
.message-head,
.message-meta {
  color: #6b7280;
  font-size: 12px;
}

.account-meta {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 6px;
  justify-content: space-between;
  min-width: 0;
}

.account-meta > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.account-delete-button {
  width: 26px;
  height: 26px;
}

.bridge-section {
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid #e5eaf0;
}

.bridge-section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.bridge-section-head div {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.bridge-section-head strong {
  font-size: 14px;
  line-height: 1.4;
}

.bridge-section-head span,
.bridge-meta {
  color: #6b7280;
  font-size: 12px;
  line-height: 1.5;
}

.bridge-meta {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.bridge-error {
  margin: 0 0 10px;
  color: #c45656;
  font-size: 12px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.message-panel {
  min-height: 420px;
}

.message-item {
  padding: 12px;
}

.message-head,
.message-meta {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
}

.message-text {
  margin: 8px 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.55;
}

.message-image-list {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 8px 0;
}

.message-image {
  width: 96px;
  margin: 0;
}

.message-image img,
.reply-image-preview {
  display: block;
  width: 100%;
  border: 1px solid #d9e0e8;
  border-radius: 6px;
  object-fit: cover;
  background: #fff;
}

.message-image img {
  height: 96px;
}

.message-image figcaption {
  min-height: 64px;
  padding: 8px;
  border: 1px solid #d9e0e8;
  border-radius: 6px;
  color: #c45656;
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.reply-form {
  max-width: 760px;
}

.reply-image-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.reply-image-row span {
  max-width: 360px;
  color: #6b7280;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reply-image-preview {
  width: 160px;
  height: 160px;
  margin-top: 10px;
}

.hidden-file-input {
  display: none;
}

@media (max-width: 920px) {
  .ai-wechat-page {
    padding: 16px;
  }

  .workspace-grid {
    grid-template-columns: 1fr;
  }
}
</style>
