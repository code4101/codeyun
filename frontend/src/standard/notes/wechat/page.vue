<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Download, Refresh, Search } from '@element-plus/icons-vue'
import StandardPagination from '@/components/StandardPagination.vue'

import {
  downloadWeChatDbMedia,
  fetchWeChatDbChats,
  fetchWeChatDbDevices,
  fetchWeChatDbMessageCount,
  fetchWeChatDbMessageTypes,
  fetchWeChatDbMessages,
  fetchWeChatDbStatus,
  syncWeChatDbFromLive,
  type WeChatDbChat,
  type WeChatDbDevice,
  type WeChatDbLiveSyncResult,
  type WeChatDbMessage,
  type WeChatDbMessageType,
  type WeChatDbResourceExport,
  type WeChatDbStatus,
} from '@/api/wechatArchive'

const DEFAULT_SELF_USERNAMES = new Set(['wxid_m1cd4f5aahut22'])
const CHAT_PAGE_SIZE_OPTIONS = [20, 50, 100, 200]
const MESSAGE_PAGE_SIZE_OPTIONS = [20, 50, 100, 200]
const route = useRoute()

const status = ref<WeChatDbStatus | null>(null)
const devices = ref<WeChatDbDevice[]>([])
const selectedDeviceId = ref('')
const chats = ref<WeChatDbChat[]>([])
const messages = ref<WeChatDbMessage[]>([])
const messageTypes = ref<WeChatDbMessageType[]>([])
const selectedUsername = ref('')
const selectedChatCache = ref<WeChatDbChat | null>(null)
const chatKeyword = ref('')
const messageKeyword = ref('')
const typeFilter = ref('')
const chatPageSize = ref(50)
const currentChatPage = ref(1)
const totalChats = ref(0)
const pageSize = ref(20)
const currentPage = ref(1)
const totalMessages = ref(0)
const loading = ref(false)
const chatLoading = ref(false)
const messageLoading = ref(false)
const resourceLoading = ref(false)
const resourceLoadingSlow = ref(false)
const liveSyncLoading = ref(false)
const lastLiveSyncResult = ref<WeChatDbLiveSyncResult | null>(null)
const messageStreamRef = ref<HTMLElement | null>(null)
const previewImage = ref<WeChatDbResourceExport | null>(null)
const mediaObjectUrls = ref<Record<string, string>>({})
const foldedListOpen = ref(false)
const suppressMessagePageChange = ref(false)
let messageRequestSerial = 0
let resourceRequestSerial = 0
let resourceSlowTimer: number | undefined
let suppressTypeFilterChange = false
const mediaObjectUrlLoading = new Set<string>()

const selectedChat = computed(() => {
  const current = chats.value.find((item) => item.username === selectedUsername.value)
  if (current) return current
  return selectedChatCache.value?.username === selectedUsername.value ? selectedChatCache.value : null
})
const selectedDevice = computed(() => devices.value.find((item) => item.id === selectedDeviceId.value) || null)
const activeDeviceId = computed(() => selectedDeviceId.value || status.value?.device_id || '')
const isQqPage = computed(() => route.path.startsWith('/notes/qq-data'))
const pageTitle = computed(() => (isQqPage.value ? 'QQ数据' : '微信数据'))
const selfUsernames = computed(() => {
  const names = new Set(DEFAULT_SELF_USERNAMES)
  if (status.value?.self_username) names.add(status.value.self_username)
  return names
})
const visibleMessages = computed(() => messages.value)
const lastChatPage = computed(() => Math.max(1, Math.ceil(totalChats.value / chatPageSize.value)))
const lastPage = computed(() => Math.max(1, Math.ceil(totalMessages.value / pageSize.value)))
const qqCacheText = computed(() => {
  if (!isQqPage.value) return ''
  const total = status.value?.structured_total ?? 0
  const chatCount = status.value?.structured_chats ?? totalChats.value
  if (total > 0) return `CodeYun缓存 ${formatNumber(total)} 条 / ${formatNumber(chatCount)} 会话`
  return status.value?.structured_ready ? 'CodeYun缓存已就绪' : '尚未写入CodeYun缓存'
})
const chatSubtitle = computed(() => {
  const current = selectedChat.value?.username || status.value?.db_storage_path || '未读取'
  return qqCacheText.value ? `${current} · ${qqCacheText.value}` : current
})
const chatSubtitleTitle = computed(() => status.value?.archive_path || status.value?.db_storage_path || '')

const WECHAT_BUILTIN_EMOJI_MAP: Record<string, string> = {
  '[微笑]': '\u{1f642}',
  '[撇嘴]': '\u{1f615}',
  '[色]': '\u{1f60d}',
  '[发呆]': '\u{1f636}',
  '[得意]': '\u{1f60e}',
  '[流泪]': '\u{1f622}',
  '[害羞]': '\u{1f60a}',
  '[闭嘴]': '\u{1f910}',
  '[睡]': '\u{1f634}',
  '[大哭]': '\u{1f62d}',
  '[尴尬]': '\u{1f613}',
  '[发怒]': '\u{1f620}',
  '[调皮]': '\u{1f61c}',
  '[呲牙]': '\u{1f601}',
  '[惊讶]': '\u{1f632}',
  '[难过]': '\u{1f61e}',
  '[酷]': '\u{1f60e}',
  '[冷汗]': '\u{1f605}',
  '[抓狂]': '\u{1f616}',
  '[吐]': '\u{1f92e}',
  '[偷笑]': '\u{1f92d}',
  '[愉快]': '\u{1f60a}',
  '[白眼]': '\u{1f644}',
  '[傲慢]': '\u{1f928}',
  '[困]': '\u{1f62a}',
  '[惊恐]': '\u{1f628}',
  '[流汗]': '\u{1f613}',
  '[憨笑]': '\u{1f604}',
  '[悠闲]': '\u{1f60c}',
  '[奋斗]': '\u{1f4aa}',
  '[咒骂]': '\u{1f92c}',
  '[疑问]': '\u{1f914}',
  '[嘘]': '\u{1f92b}',
  '[晕]': '\u{1f635}',
  '[衰]': '\u{1f635}',
  '[骷髅]': '\u{1f480}',
  '[敲打]': '\u{1f528}',
  '[再见]': '\u{1f44b}',
  '[擦汗]': '\u{1f605}',
  '[抠鼻]': '\u{1f443}',
  '[鼓掌]': '\u{1f44f}',
  '[坏笑]': '\u{1f608}',
  '[左哼哼]': '\u{1f624}',
  '[右哼哼]': '\u{1f624}',
  '[哈欠]': '\u{1f971}',
  '[鄙视]': '\u{1f612}',
  '[委屈]': '\u{1f97a}',
  '[快哭了]': '\u{1f97a}',
  '[阴险]': '\u{1f60f}',
  '[亲亲]': '\u{1f618}',
  '[可怜]': '\u{1f97a}',
  '[拥抱]': '\u{1fac2}',
  '[强]': '\u{1f44d}',
  '[弱]': '\u{1f44e}',
  '[握手]': '\u{1f91d}',
  '[胜利]': '\u270c\ufe0f',
  '[抱拳]': '\u{1f64f}',
  '[勾引]': '\u261d\ufe0f',
  '[拳头]': '\u270a',
  '[OK]': '\u{1f44c}',
  '[合十]': '\u{1f64f}',
  '[玫瑰]': '\u{1f339}',
  '[凋谢]': '\u{1f940}',
  '[嘴唇]': '\u{1f48b}',
  '[爱心]': '\u2764\ufe0f',
  '[心碎]': '\u{1f494}',
  '[蛋糕]': '\u{1f382}',
  '[炸弹]': '\u{1f4a3}',
  '[便便]': '\u{1f4a9}',
  '[月亮]': '\u{1f319}',
  '[太阳]': '\u2600\ufe0f',
  '[礼物]': '\u{1f381}',
  '[红包]': '\u{1f9e7}',
  '[庆祝]': '\u{1f389}',
  '[發]': '\u{1f9e7}',
  '[福]': '\u{1f9e7}',
  '[奸笑]': '\u{1f60f}',
  '[机智]': '\u{1f609}',
  '[皱眉]': '\u{1f928}',
  '[耶]': '\u270c\ufe0f',
  '[吃瓜]': '\u{1f349}',
  '[加油]': '\u{1f4aa}',
  '[Facepalm]': '\u{1f926}',
  '[Onlooker]': '\u{1f440}',
  '[Lol]': '\u{1f606}',
  '[Terror]': '\u{1f631}',
  '[Concerned]': '\u{1f61f}',
  '[Hurt]': '\u{1f915}',
  '/::>': '\u{1f60a}',
  '/:dig': '\u{1f443}',
  '/:moon': '\u{1f319}',
}

const messageTypeOptions = computed(() => [
  { label: '全部类型', value: '' },
  ...messageTypes.value.map((item) => ({
    label: `${typeLabel(item.local_type)} ${formatNumber(item.count)}`,
    value: String(item.local_type),
  })),
])

function formatNumber(value: number | null | undefined) {
  return Number(value || 0).toLocaleString()
}

function toDate(value: number | null | undefined) {
  if (!value) return null
  const date = new Date(value * 1000)
  return Number.isNaN(date.getTime()) ? null : date
}

function formatChatTime(value: number | null | undefined) {
  const date = toDate(value)
  if (!date) return ''
  const now = new Date()
  const sameDay = date.toDateString() === now.toDateString()
  if (sameDay) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }
  if (date.getFullYear() === now.getFullYear()) {
    return date.toLocaleDateString([], { month: '2-digit', day: '2-digit' })
  }
  return date.toLocaleDateString()
}

function formatMessageTime(value: number | null | undefined) {
  const date = toDate(value)
  if (!date) return ''
  const now = new Date()
  const options: Intl.DateTimeFormatOptions = {
    hour: '2-digit',
    minute: '2-digit',
  }
  if (date.toDateString() !== now.toDateString()) {
    options.month = '2-digit'
    options.day = '2-digit'
  }
  if (date.getFullYear() !== now.getFullYear()) {
    options.year = 'numeric'
  }
  return date.toLocaleString([], options)
}

function typeLabel(value: number | string | null | undefined) {
  const key = Number(value)
  const map: Record<number, string> = {
    1: '文本',
    3: '图片',
    34: '语音',
    37: '好友验证',
    42: '名片',
    43: '视频',
    47: '表情',
    48: '位置',
    49: '链接/文件',
    50: '通话',
    51: '状态',
    10000: '系统',
    10002: '撤回',
  }
  return Number.isFinite(key) ? (map[key] || String(key)) : '-'
}

function deviceSourceText(value: string | null | undefined) {
  if (value === 'wechat_3') return '微信 3.x'
  if (value === 'wechat_4') return '微信 4.x'
  if (value === 'tim_legacy') return 'QQ/TIM'
  return '微信数据'
}

function isCurrentPageDevice(device: WeChatDbDevice) {
  return isQqPage.value ? device.source_format === 'tim_legacy' : device.source_format !== 'tim_legacy'
}

function deviceOptionLabel(device: WeChatDbDevice) {
  const state = device.ready ? '' : ' · 未就绪'
  return `${device.label} · ${deviceSourceText(device.source_format)}${state}`
}

function liveSyncTitle() {
  if (lastLiveSyncResult.value) {
    if (isQqPage.value && lastLiveSyncResult.value.structured) {
      const structured = lastLiveSyncResult.value.structured
      return `上次同步 ${lastLiveSyncResult.value.elapsed_seconds}s，新增 ${structured.inserted} 条，共 ${structured.total} 条`
    }
    return `上次同步 ${lastLiveSyncResult.value.elapsed_seconds}s，新增资源 ${lastLiveSyncResult.value.media?.new_files ?? 0}`
  }
  if (selectedDevice.value?.can_sync_live) {
    return isQqPage.value ? '读取运行中的 TIM，并把 QQ 消息结构化存入 CodeYun' : '复制本机微信数据库快照、解密并导出新增资源'
  }
  return isQqPage.value ? '需要本机 TIM 正在运行' : '只能同步本机微信数据'
}

function messageType(row: WeChatDbMessage) {
  return row.local_type_normalized ?? row.local_type
}

function chatPreview(chat: WeChatDbChat) {
  return renderWechatEmojiText(chat.summary || chat.table_name || chat.username)
}

function avatarText(value: string | null | undefined) {
  const text = (value || '').trim()
  if (!text) return '?'
  return Array.from(text).slice(0, 2).join('')
}

function avatarUrl(value: string | null | undefined) {
  return value || ''
}

function extractXmlTag(text: string, tag: string) {
  const match = text.match(new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`, 'i'))
  return match?.[1]?.replace(/<!\[CDATA\[|\]\]>/g, '').trim() || ''
}

function resourceSuffix(row: WeChatDbMessage) {
  if (!row.resource?.resource_count) return ''
  return ` · ${row.resource.resource_count} 个资源 / ${formatNumber(row.resource.total_size)}B`
}

function renderWechatEmojiText(text: string) {
  return text.replace(/\[[^\]\r\n]{1,16}\]|\/::>|\/:dig|\/:moon/g, (token) => WECHAT_BUILTIN_EMOJI_MAP[token] || token)
}

function resourceExports(row: WeChatDbMessage) {
  return row.resource?.items?.map((item) => item.export).filter(Boolean) as WeChatDbResourceExport[] | undefined
}

function mediaResources(row: WeChatDbMessage, kind?: WeChatDbResourceExport['kind']) {
  const items = resourceExports(row) || []
  return kind ? items.filter((item) => item.kind === kind) : items
}

function inlineMediaResources(row: WeChatDbMessage) {
  return mediaResources(row).filter((item) => item.kind === 'video' || (item.kind === 'image' && !isWechatImageDat(item)))
}

function downloadableResources(row: WeChatDbMessage) {
  const primaryFile = appMessageFile(row)?.download_name
  return mediaResources(row).filter(
    (item) => item.download_name !== primaryFile && item.kind === 'file',
  )
}

function mediaObjectKey(item: WeChatDbResourceExport) {
  return `${activeDeviceId.value || ''}:${item.download_name}`
}

function mediaObjectUrl(item: WeChatDbResourceExport) {
  return mediaObjectUrls.value[mediaObjectKey(item)] || ''
}

const previewImageUrl = computed(() => (previewImage.value ? mediaObjectUrl(previewImage.value) : ''))

function resourceName(item: WeChatDbResourceExport) {
  if (item.kind === 'image') return isWechatImageDat(item) ? '微信图片缓存' : '图片'
  if (item.kind === 'video') return '视频'
  if (item.kind === 'file') return item.file_name
  return item.file_name
}

function isWechatImageDat(item: WeChatDbResourceExport) {
  return item.kind === 'image' && /\.dat$/i.test(item.file_name || item.download_name)
}

function appMessageTitle(row: WeChatDbMessage) {
  return renderWechatEmojiText(row.appmsg?.title || '')
}

function appMessageDescription(row: WeChatDbMessage) {
  const text = row.appmsg?.description || ''
  return renderWechatEmojiText(text)
}

function appMessageUrl(row: WeChatDbMessage) {
  return row.appmsg?.url || ''
}

function appMessageFile(row: WeChatDbMessage) {
  return mediaResources(row, 'file')[0]
}

function appMessageKind(row: WeChatDbMessage) {
  const appType = row.appmsg?.app_type
  if (appType === 6 || appMessageFile(row)) return '文件'
  if (appType === 57) return '引用'
  if (appType === 4) return '视频链接'
  if (appType === 5) return '链接'
  if (appType === 36) return '小程序'
  if (appType === 51) return '特殊内容'
  return typeLabel(messageType(row))
}

function quoteContent(row: WeChatDbMessage) {
  const fallback = row.appmsg?.refer_content || ''
  const fallbackText = /<\/?[a-z][\s\S]*>/i.test(fallback) ? '' : fallback
  return renderWechatEmojiText(row.appmsg?.refer?.content || fallbackText)
}

function quoteAuthor(row: WeChatDbMessage) {
  return row.appmsg?.refer?.display_name || row.appmsg?.refer?.from_user || ''
}

function quoteLabel(row: WeChatDbMessage) {
  const author = quoteAuthor(row)
  const content = quoteContent(row)
  return author ? `${author}: ${content}` : content
}

function openAppMessage(row: WeChatDbMessage) {
  const file = appMessageFile(row)
  if (file) {
    void downloadResource(file)
    return
  }
  const url = appMessageUrl(row)
  if (url) {
    window.open(url, '_blank', 'noopener,noreferrer')
  }
}

function shouldShowAppCard(row: WeChatDbMessage) {
  return row.appmsg?.app_type !== 57 && Boolean(row.appmsg?.title || row.appmsg?.description || row.appmsg?.url || appMessageFile(row))
}

function shouldShowQuoteMessage(row: WeChatDbMessage) {
  return row.appmsg?.app_type === 57 && Boolean(appMessageTitle(row) || quoteContent(row))
}

function compactXmlContent(text: string, row: WeChatDbMessage) {
  const lower = text.toLowerCase()
  if (!lower.includes('<msg') && !lower.includes('<?xml') && !lower.includes('<appmsg')) return ''
  if (lower.includes('<appmsg')) {
    const title = extractXmlTag(text, 'title')
    const description = extractXmlTag(text, 'des')
    if (title) return `[${typeLabel(messageType(row))}] ${title}${description ? `\n${description}` : ''}`
  }
  if (lower.includes('<img ')) return '<图片>'
  if (lower.includes('<videomsg')) return '<视频>'
  if (lower.includes('<voicemsg')) return '<语音>'
  if (lower.includes('<emoji')) return '<表情>'
  if (lower.includes('<location')) return '<位置>'
  const revoke = extractXmlTag(text, 'replacemsg')
  if (revoke) return revoke
  return `<${typeLabel(messageType(row))}>`
}

function stripSenderPrefix(text: string, row: WeChatDbMessage) {
  let result = text.trim()
  const candidates = [row.sender_username, row.sender_name].filter(Boolean) as string[]
  for (const candidate of candidates) {
    for (const separator of [':\n', ':\r\n', ': ']) {
      const prefix = `${candidate}${separator}`
      if (result.startsWith(prefix)) {
        result = result.slice(prefix.length).trim()
      }
    }
  }
  return result
}

function messageContent(row: WeChatDbMessage) {
  if (shouldShowAppCard(row)) return ''
  if (shouldShowQuoteMessage(row)) return appMessageTitle(row)
  const rawText = row.message_text || row.message_content || row.compress_content || row.source_text || row.source || ''
  const text = stripSenderPrefix(rawText, row)
  const resource = resourceSuffix(row)
  const compactXml = compactXmlContent(text, row)
  if (compactXml) return renderWechatEmojiText(`${compactXml}${resource}`)
  if (text) return renderWechatEmojiText(`${text}${resource}`)
  return `<${typeLabel(messageType(row))}${resource}>`
}

function shouldShowMessageBubble(row: WeChatDbMessage) {
  const content = messageContent(row)
  return Boolean(content) && (!inlineMediaResources(row).length || !/^<图片|^<视频|^<表情/.test(content))
}

function isOutgoing(row: WeChatDbMessage) {
  return selfUsernames.value.has(row.sender_username || '')
}

function showSenderName(row: WeChatDbMessage) {
  return selectedChat.value?.chat_type === 'chatroom' && !isOutgoing(row)
}

function shouldShowTime(index: number) {
  const row = visibleMessages.value[index]
  const previous = visibleMessages.value[index - 1]
  if (!row || !row.create_time) return false
  if (!previous || !previous.create_time) return true
  const gapSeconds = Math.abs(row.create_time - previous.create_time)
  const rowDate = toDate(row.create_time)
  const previousDate = toDate(previous.create_time)
  return gapSeconds > 600 || rowDate?.toDateString() !== previousDate?.toDateString()
}

function getErrorMessage(error: unknown) {
  const candidate = error as { response?: { data?: { detail?: string } }; message?: string }
  return candidate.response?.data?.detail || candidate.message || '读取失败'
}

function clearMediaObjectUrls() {
  Object.values(mediaObjectUrls.value).forEach((url) => URL.revokeObjectURL(url))
  mediaObjectUrls.value = {}
  mediaObjectUrlLoading.clear()
}

async function ensureMediaObjectUrl(item: WeChatDbResourceExport) {
  const key = mediaObjectKey(item)
  if (mediaObjectUrls.value[key] || mediaObjectUrlLoading.has(key)) return
  mediaObjectUrlLoading.add(key)
  try {
    const blob = await downloadWeChatDbMedia(item, activeDeviceId.value || undefined)
    const url = URL.createObjectURL(blob)
    const previous = mediaObjectUrls.value[key]
    if (previous) URL.revokeObjectURL(previous)
    mediaObjectUrls.value = { ...mediaObjectUrls.value, [key]: url }
  } finally {
    mediaObjectUrlLoading.delete(key)
  }
}

async function ensureInlineMediaObjectUrls() {
  const items = visibleMessages.value.flatMap((row) => inlineMediaResources(row))
  await Promise.allSettled(items.map((item) => ensureMediaObjectUrl(item)))
}

async function downloadResource(item: WeChatDbResourceExport) {
  try {
    const blob = await downloadWeChatDbMedia(item, activeDeviceId.value || undefined)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = item.file_name
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  }
}

function handleInlineMediaClick(item: WeChatDbResourceExport) {
  if (item.kind === 'image' && mediaObjectUrl(item)) {
    previewImage.value = item
    return
  }
  void downloadResource(item)
}

function closeImagePreview() {
  previewImage.value = null
}

function handlePreviewKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && previewImage.value) {
    closeImagePreview()
  }
}

async function loadInlineMedia() {
  await ensureInlineMediaObjectUrls()
  if (currentPage.value === lastPage.value) {
    await scrollMessagesToBottom()
  }
}

async function loadStatus() {
  status.value = await fetchWeChatDbStatus({ device_id: activeDeviceId.value || undefined })
}

async function loadDevices() {
  const payload = await fetchWeChatDbDevices()
  devices.value = payload.items.filter(isCurrentPageDevice)
  const selectedStillExists = devices.value.some((item) => item.id === selectedDeviceId.value)
  if (selectedDeviceId.value && selectedStillExists) return
  selectedDeviceId.value = devices.value.find((item) => item.current)?.id || devices.value.find((item) => item.ready)?.id || devices.value[0]?.id || ''
}

async function loadChats() {
  chatLoading.value = true
  try {
    const payload = await fetchWeChatDbChats({
      device_id: activeDeviceId.value || undefined,
      q: chatKeyword.value.trim() || undefined,
      limit: chatPageSize.value,
      offset: (currentChatPage.value - 1) * chatPageSize.value,
      scope: foldedListOpen.value ? 'folded' : 'main',
    })
    chats.value = payload.items
    totalChats.value = payload.total
    if (currentChatPage.value > lastChatPage.value) {
      currentChatPage.value = lastChatPage.value
      await loadChats()
      return
    }
  } finally {
    chatLoading.value = false
  }
}

async function loadMessageTypes() {
  const username = selectedUsername.value
  if (!username) {
    messageTypes.value = []
    return
  }
  const payload = await fetchWeChatDbMessageTypes({ device_id: activeDeviceId.value || undefined, chat_username: username })
  messageTypes.value = payload.items
  if (typeFilter.value && !messageTypes.value.some((item) => String(item.local_type) === typeFilter.value)) {
    typeFilter.value = ''
  }
}

async function loadMessages() {
  const username = selectedUsername.value
  const requestId = ++messageRequestSerial
  const targetPage = currentPage.value
  if (!username) {
    messages.value = []
    totalMessages.value = 0
    resourceRequestSerial += 1
    resourceLoading.value = false
    return
  }
  messageLoading.value = true
  try {
    const payload = await fetchWeChatDbMessages({
      device_id: activeDeviceId.value || undefined,
      chat_username: username,
      q: messageKeyword.value.trim() || undefined,
      message_type: typeFilter.value || undefined,
      limit: pageSize.value,
      offset: (targetPage - 1) * pageSize.value,
      order: 'asc',
      include_resources: false,
      known_total: totalMessages.value || undefined,
    })
    if (requestId !== messageRequestSerial || username !== selectedUsername.value) return
    if (!payload.items.length && payload.total > 0 && targetPage > Math.ceil(payload.total / pageSize.value)) {
      currentPage.value = Math.max(1, Math.ceil(payload.total / pageSize.value))
      void loadMessages().then(scrollMessagesToBottom)
      return
    }
    messages.value = payload.items
    totalMessages.value = payload.total
    if (isQqPage.value) {
      resourceRequestSerial += 1
      resourceLoading.value = false
      resourceLoadingSlow.value = false
      return
    }
    void loadMessageResources(requestId, username, targetPage)
  } catch (error) {
    if (requestId !== messageRequestSerial) return
    ElMessage.error(getErrorMessage(error))
  } finally {
    if (requestId === messageRequestSerial) {
      messageLoading.value = false
    }
  }
}

async function setMessagePageSilently(page: number) {
  suppressMessagePageChange.value = true
  currentPage.value = page
  await nextTick()
  suppressMessagePageChange.value = false
}

function handleMessagePageChange() {
  if (suppressMessagePageChange.value) return
  void loadMessages().then(scrollMessagesToBottom)
}

function handleMessagePageSizeChange() {
  if (suppressMessagePageChange.value) return
  reloadMessages()
}

async function loadMessageResources(requestId: number, username: string, targetPage: number) {
  const resourceId = ++resourceRequestSerial
  resourceLoading.value = true
  resourceLoadingSlow.value = false
  window.clearTimeout(resourceSlowTimer)
  resourceSlowTimer = window.setTimeout(() => {
    if (resourceId === resourceRequestSerial && resourceLoading.value) {
      resourceLoadingSlow.value = true
    }
  }, 2500)
  try {
    const payload = await fetchWeChatDbMessages({
      device_id: activeDeviceId.value || undefined,
      chat_username: username,
      q: messageKeyword.value.trim() || undefined,
      message_type: typeFilter.value || undefined,
      limit: pageSize.value,
      offset: (targetPage - 1) * pageSize.value,
      order: 'asc',
      include_resources: true,
      known_total: totalMessages.value || undefined,
    })
    if (requestId !== messageRequestSerial || resourceId !== resourceRequestSerial || username !== selectedUsername.value) return
    const byId = new Map(payload.items.map((item) => [item.local_id, item]))
    messages.value = messages.value.map((item) => byId.get(item.local_id) || item)
    totalMessages.value = payload.total
    await loadInlineMedia()
  } catch (error) {
    if (resourceId === resourceRequestSerial) {
      ElMessage.warning(`图片资源仍在本地同步/解析，可稍后刷新：${getErrorMessage(error)}`)
    }
  } finally {
    if (resourceId === resourceRequestSerial) {
      window.clearTimeout(resourceSlowTimer)
      resourceLoading.value = false
      resourceLoadingSlow.value = false
    }
  }
}

async function loadLatestMessages() {
  const username = selectedUsername.value
  const requestId = ++messageRequestSerial
  if (!username) {
    messages.value = []
    totalMessages.value = 0
    resourceRequestSerial += 1
    resourceLoading.value = false
    return
  }
  messageLoading.value = true
  messages.value = []
  clearMediaObjectUrls()
  try {
    const keyword = messageKeyword.value.trim()
    const selectedMessageCount = selectedChat.value?.username === username ? selectedChat.value.message_count : null
    if (!keyword && !typeFilter.value && selectedMessageCount != null) {
      totalMessages.value = selectedMessageCount
    } else {
      const countPayload = await fetchWeChatDbMessageCount({
        device_id: activeDeviceId.value || undefined,
        chat_username: username,
        q: keyword || undefined,
        message_type: typeFilter.value || undefined,
      })
      if (requestId !== messageRequestSerial || username !== selectedUsername.value) return
      totalMessages.value = countPayload.total
    }
    const targetPage = Math.max(1, Math.ceil(totalMessages.value / pageSize.value))
    await setMessagePageSilently(targetPage)
    const payload = await fetchWeChatDbMessages({
      device_id: activeDeviceId.value || undefined,
      chat_username: username,
      q: keyword || undefined,
      message_type: typeFilter.value || undefined,
      limit: pageSize.value,
      offset: (targetPage - 1) * pageSize.value,
      order: 'asc',
      include_resources: false,
      known_total: totalMessages.value || undefined,
    })
    if (requestId !== messageRequestSerial || username !== selectedUsername.value) return
    messages.value = payload.items
    totalMessages.value = payload.total
    if (isQqPage.value) {
      resourceRequestSerial += 1
      resourceLoading.value = false
      resourceLoadingSlow.value = false
      await scrollMessagesToBottom()
      return
    }
    void loadMessageResources(requestId, username, targetPage)
  } catch (error) {
    if (requestId !== messageRequestSerial) return
    ElMessage.error(getErrorMessage(error))
  } finally {
    if (requestId === messageRequestSerial) {
      messageLoading.value = false
    }
  }
  await scrollMessagesToBottom()
}

async function scrollMessagesToBottom() {
  await nextTick()
  const el = messageStreamRef.value
  if (el) {
    el.scrollTop = el.scrollHeight
  }
}

async function refreshAll() {
  loading.value = true
  try {
    await loadDevices()
    await loadStatus()
    await loadChats()
    if (selectedUsername.value) {
      await loadLatestMessages()
      void loadMessageTypes()
    }
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    loading.value = false
  }
}

async function syncLiveData() {
  liveSyncLoading.value = true
  try {
    const result = await syncWeChatDbFromLive({ device_id: activeDeviceId.value || undefined })
    lastLiveSyncResult.value = result
    await loadDevices()
    await refreshAll()
    const copied = result.copy.copied
    const decrypted = result.decrypt.decrypted
    const newMedia = result.media?.new_files ?? 0
    ElMessage.success(`同步完成：复制 ${copied}，解密 ${decrypted}，新增资源 ${newMedia}`)
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    liveSyncLoading.value = false
  }
}

function resetConversationState() {
  chats.value = []
  messages.value = []
  clearMediaObjectUrls()
  messageTypes.value = []
  selectedUsername.value = ''
  selectedChatCache.value = null
  currentChatPage.value = 1
  currentPage.value = 1
  totalChats.value = 0
  totalMessages.value = 0
  foldedListOpen.value = false
  typeFilter.value = ''
  lastLiveSyncResult.value = null
  messageRequestSerial += 1
  resourceRequestSerial += 1
  resourceLoading.value = false
  resourceLoadingSlow.value = false
}

function handleDeviceChange() {
  resetConversationState()
  void refreshAll()
}

function reloadChats() {
  currentChatPage.value = 1
  void loadChats()
}

function reloadMessages() {
  void loadLatestMessages().then(loadMessageTypes)
}

function selectChat(chat: WeChatDbChat) {
  const changed = chat.username !== selectedUsername.value
  if (changed) {
    suppressMessagePageChange.value = true
    suppressTypeFilterChange = true
    messageRequestSerial += 1
    resourceRequestSerial += 1
    resourceLoading.value = false
    resourceLoadingSlow.value = false
    messages.value = []
    messageTypes.value = []
    typeFilter.value = ''
    currentPage.value = 1
    void nextTick(() => {
      suppressMessagePageChange.value = false
      suppressTypeFilterChange = false
    })
  }
  selectedChatCache.value = chat
  selectedUsername.value = chat.username
  void loadLatestMessages().then(loadMessageTypes)
}

function loadChatPage() {
  void loadChats()
}

function openFoldedList() {
  foldedListOpen.value = true
  currentChatPage.value = 1
  void loadChats()
}

function closeFoldedList() {
  foldedListOpen.value = false
  currentChatPage.value = 1
  void loadChats()
}

watch(typeFilter, () => {
  if (suppressTypeFilterChange) return
  void loadLatestMessages()
})

watch(visibleMessages, () => {
  void loadInlineMedia()
})

onMounted(() => {
  window.addEventListener('keydown', handlePreviewKeydown)
  void refreshAll()
})

onBeforeUnmount(() => {
  window.clearTimeout(resourceSlowTimer)
  window.removeEventListener('keydown', handlePreviewKeydown)
})
</script>

<template>
  <div class="wechat-db-page">
    <aside class="conversation-sidebar">
      <div class="device-switcher">
        <el-select
          v-model="selectedDeviceId"
          class="device-select"
          size="small"
          placeholder="选择设备"
          @change="handleDeviceChange"
        >
          <el-option
            v-for="device in devices"
            :key="device.id"
            :label="deviceOptionLabel(device)"
            :value="device.id"
            :disabled="!device.ready && !device.current"
          />
        </el-select>
      </div>
      <div v-if="foldedListOpen" class="folded-list-header">
        <button type="button" class="folded-back" title="返回" @click="closeFoldedList">
          <el-icon><ArrowLeft /></el-icon>
        </button>
        <span>折叠的聊天</span>
      </div>
      <div class="sidebar-search">
        <el-input
          v-model="chatKeyword"
          :prefix-icon="Search"
          size="small"
          clearable
          placeholder="搜索"
          @keyup.enter="reloadChats"
          @clear="reloadChats"
        />
        <el-button :icon="Refresh" :loading="loading" size="small" text @click="refreshAll" />
      </div>
      <div class="sidebar-meta">
        <span>{{ formatNumber(totalChats) }} 个会话</span>
        <span>第 {{ currentChatPage }} 页</span>
      </div>
      <div v-loading="chatLoading" class="conversation-list">
        <button
          v-for="chat in chats"
          :key="chat.username"
          type="button"
          class="conversation-row"
          :class="{ active: !chat.is_folded_entry && chat.username === selectedUsername, 'folded-entry': chat.is_folded_entry }"
          @click="chat.is_folded_entry ? openFoldedList() : selectChat(chat)"
        >
          <template v-if="chat.is_folded_entry">
            <div class="folded-entry-icon">{{ chat.message_count }}</div>
            <div class="conversation-main">
              <div class="conversation-title">
                <strong>折叠的聊天</strong>
                <time>{{ formatChatTime(chat.last_time) }}</time>
              </div>
              <div class="conversation-preview">
                <span>{{ chatPreview(chat) }}</span>
                <em>{{ formatNumber(chat.unread_count) }}</em>
              </div>
            </div>
          </template>
          <template v-else>
            <div class="avatar" :class="{ group: chat.chat_type === 'chatroom' }">
              <img v-if="avatarUrl(chat.avatar_data_url)" :src="avatarUrl(chat.avatar_data_url)" :alt="chat.name" />
              <span v-else>{{ avatarText(chat.name) }}</span>
            </div>
            <div class="conversation-main">
              <div class="conversation-title">
                <strong>{{ chat.name }}</strong>
                <time>{{ formatChatTime(chat.last_time) }}</time>
              </div>
              <div class="conversation-preview">
                <span>{{ chatPreview(chat) }}</span>
                <em v-if="chat.message_count">{{ formatNumber(chat.message_count) }}</em>
              </div>
            </div>
          </template>
        </button>
        <div v-if="!chats.length" class="empty-state">暂无会话</div>
      </div>
      <div class="conversation-footer">
        <StandardPagination
          v-model:page="currentChatPage"
          v-model:page-size="chatPageSize"
          :page-size-options="CHAT_PAGE_SIZE_OPTIONS"
          :total="totalChats"
          @page-change="loadChatPage"
          @page-size-change="reloadChats"
        />
      </div>
    </aside>

    <section class="chat-shell">
      <header class="chat-header">
        <div class="chat-title">
          <h1>{{ selectedChat?.name || pageTitle }}</h1>
          <span :title="chatSubtitleTitle">
            {{ chatSubtitle }}
          </span>
        </div>
        <div class="chat-tools">
          <el-input
            v-model="messageKeyword"
            class="message-search"
            :prefix-icon="Search"
            size="small"
            clearable
            placeholder="搜索消息"
            @keyup.enter="reloadMessages"
            @clear="reloadMessages"
          />
          <el-select v-model="typeFilter" class="type-select" size="small">
            <el-option
              v-for="option in messageTypeOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
            />
          </el-select>
          <el-button :icon="Search" size="small" plain @click="reloadMessages">查询</el-button>
          <el-button
            :icon="Refresh"
            :loading="liveSyncLoading"
            :title="liveSyncTitle()"
            size="small"
            plain
            :disabled="!selectedDevice?.can_sync_live"
            @click="syncLiveData"
          >
            同步本机
          </el-button>
        </div>
      </header>

      <main ref="messageStreamRef" v-loading="messageLoading" class="message-stream">
        <div v-if="resourceLoading" class="resource-loading" :class="{ slow: resourceLoadingSlow }">
          {{ resourceLoadingSlow ? '图片资源还在解析，消息可先查看' : '正在补齐图片资源...' }}
        </div>
        <div v-if="!messageLoading && !visibleMessages.length" class="stream-empty">暂无消息</div>
        <template v-for="(row, index) in visibleMessages" :key="row.local_id">
          <div v-if="shouldShowTime(index)" class="time-separator">
            {{ formatMessageTime(row.create_time) }}
          </div>
          <div class="message-row" :class="{ outgoing: isOutgoing(row) }">
            <div v-if="!isOutgoing(row)" class="avatar message-avatar">
              <img
                v-if="avatarUrl(row.sender_avatar_data_url)"
                :src="avatarUrl(row.sender_avatar_data_url)"
                :alt="row.sender_name || row.sender_username || ''"
              />
              <span v-else>{{ avatarText(row.sender_name || row.sender_username) }}</span>
            </div>
            <div class="bubble-wrap">
              <div v-if="showSenderName(row)" class="sender-name">
                {{ row.sender_name || row.sender_username }}
              </div>
              <button
                v-if="shouldShowAppCard(row)"
                type="button"
                class="app-message-card"
                :class="{ clickable: Boolean(appMessageUrl(row) || appMessageFile(row)) }"
                @click="openAppMessage(row)"
              >
                <strong>{{ appMessageTitle(row) || appMessageKind(row) }}</strong>
                <span v-if="appMessageDescription(row)">{{ appMessageDescription(row) }}</span>
                <em>
                  {{ appMessageKind(row) }}
                  <template v-if="appMessageFile(row)">
                    · {{ resourceName(appMessageFile(row)!) }} · {{ formatNumber(appMessageFile(row)!.size) }}B
                  </template>
                  <template v-else-if="row.appmsg?.total_size">
                    · {{ formatNumber(row.appmsg.total_size) }}B
                  </template>
                </em>
              </button>
              <div v-if="shouldShowMessageBubble(row)" class="message-bubble" :title="typeLabel(messageType(row))">
                {{ messageContent(row) }}
                <div v-if="shouldShowQuoteMessage(row) && quoteLabel(row)" class="quote-preview">
                  {{ quoteLabel(row) }}
                </div>
              </div>
              <div v-if="inlineMediaResources(row).length" class="inline-media-list">
                <button
                  v-for="item in inlineMediaResources(row)"
                  :key="item.download_name"
                  type="button"
                  class="inline-media"
                  :class="item.kind"
                  :title="item.stored_path"
                  @click="handleInlineMediaClick(item)"
                >
                  <img v-if="item.kind === 'image' && mediaObjectUrl(item)" :src="mediaObjectUrl(item)" :alt="item.file_name" />
                  <video v-else-if="item.kind === 'video' && mediaObjectUrl(item)" :src="mediaObjectUrl(item)" controls />
                  <span v-else>{{ resourceName(item) }}</span>
                </button>
              </div>
              <div v-if="downloadableResources(row).length" class="resource-actions">
                <button
                  v-for="item in downloadableResources(row)"
                  :key="`${item.kind}-${item.file_name}`"
                  type="button"
                  class="resource-button"
                  :title="item.stored_path"
                  @click="downloadResource(item)"
                >
                  <el-icon><Download /></el-icon>
                  <span>{{ resourceName(item) }}</span>
                  <em>{{ formatNumber(item.size) }}B</em>
                </button>
              </div>
            </div>
            <div v-if="isOutgoing(row)" class="avatar message-avatar self">我</div>
          </div>
        </template>
      </main>

      <footer class="chat-footer">
        <span>第 {{ currentPage }} 页 · {{ formatNumber(totalMessages) }} 条</span>
        <StandardPagination
          v-model:page="currentPage"
          v-model:page-size="pageSize"
          :page-size-options="MESSAGE_PAGE_SIZE_OPTIONS"
          :total="totalMessages"
          @page-change="handleMessagePageChange"
          @page-size-change="handleMessagePageSizeChange"
        />
      </footer>
    </section>

    <div v-if="previewImage && previewImageUrl" class="image-preview" @click.self="closeImagePreview">
      <button type="button" class="image-preview-close" aria-label="关闭预览" @click="closeImagePreview">×</button>
      <img :src="previewImageUrl" :alt="previewImage.file_name" />
      <button type="button" class="image-preview-download" @click="downloadResource(previewImage)">下载</button>
    </div>
  </div>
</template>

<style scoped>
.wechat-db-page {
  display: grid;
  grid-template-columns: 340px minmax(0, 1fr);
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: #ededed;
  color: #1f2937;
}

.conversation-sidebar {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  border-right: 1px solid #d6d6d6;
  background: #f7f7f7;
}

.device-switcher {
  flex: 0 0 auto;
  padding: 12px 12px 0;
}

.sidebar-search,
.chat-header,
.chat-tools,
.conversation-title,
.conversation-preview,
.message-row,
.chat-footer {
  display: flex;
  align-items: center;
}

.sidebar-search {
  flex: 0 0 auto;
  gap: 8px;
  padding: 9px 12px;
}

.folded-list-header {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  height: 48px;
  padding: 0 12px;
  border-bottom: 1px solid #e5e7eb;
  background: #f7f7f7;
  color: #111827;
  font-size: 15px;
  font-weight: 500;
}

.folded-back {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  background: transparent;
  color: #374151;
  cursor: pointer;
}

.folded-back:hover {
  background: #eeeeee;
}

.sidebar-meta {
  display: flex;
  flex: 0 0 auto;
  justify-content: space-between;
  padding: 0 14px 8px;
  color: #8a8f98;
  font-size: 12px;
}

.conversation-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}

.conversation-footer {
  display: flex;
  flex: 0 0 auto;
  justify-content: center;
  padding: 8px 6px;
  border-top: 1px solid #e5e7eb;
  background: #f7f7f7;
}

.conversation-row {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  gap: 10px;
  width: 100%;
  min-height: 68px;
  padding: 10px 12px;
  border: 0;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.conversation-row:hover {
  background: #eeeeee;
}

.conversation-row.active {
  background: #dcdcdc;
}

.conversation-row.folded-entry {
  background: #f1f1f1;
}

.folded-entry-icon {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  background: #e5e7eb;
  color: #374151;
  font-size: 13px;
  font-weight: 650;
}

.avatar {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  overflow: hidden;
  background: #dbeafe;
  color: #1d4ed8;
  font-size: 13px;
  font-weight: 650;
}

.avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.avatar.group {
  background: #dcfce7;
  color: #15803d;
}

.conversation-main {
  min-width: 0;
}

.conversation-title {
  justify-content: space-between;
  gap: 10px;
}

.conversation-title strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #111827;
  font-size: 14px;
  font-weight: 500;
}

.conversation-title time {
  flex: 0 0 auto;
  color: #9ca3af;
  font-size: 12px;
}

.conversation-preview {
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
}

.conversation-preview span {
  min-width: 0;
  overflow: hidden;
  color: #8a8f98;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conversation-preview em {
  flex: 0 0 auto;
  color: #111827;
  font-size: 12px;
  font-style: normal;
}

.chat-shell {
  display: grid;
  grid-template-rows: 64px minmax(0, 1fr) 54px;
  min-width: 0;
  min-height: 0;
  background: #f2f2f2;
}

.chat-header {
  justify-content: space-between;
  gap: 16px;
  padding: 0 20px;
  border-bottom: 1px solid #d8d8d8;
  background: #f5f5f5;
}

.chat-title {
  min-width: 0;
}

.chat-title h1 {
  margin: 0;
  color: #111827;
  font-size: 17px;
  font-weight: 500;
  line-height: 1.35;
}

.chat-title span {
  display: block;
  max-width: 54vw;
  margin-top: 4px;
  overflow: hidden;
  color: #8a8f98;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-tools {
  flex: 0 0 auto;
  gap: 8px;
}

.message-search {
  width: 220px;
}

.device-select {
  width: 100%;
}

.type-select {
  width: 150px;
}

.message-stream {
  min-height: 0;
  padding: 20px 28px 28px;
  overflow: auto;
}

.resource-loading {
  position: sticky;
  top: 0;
  z-index: 2;
  width: fit-content;
  max-width: 100%;
  margin: 0 auto 12px;
  padding: 5px 10px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.92);
  color: #6b7280;
  font-size: 12px;
}

.resource-loading.slow {
  color: #b45309;
}

.stream-empty,
.empty-state {
  padding: 24px 14px;
  color: #9ca3af;
  font-size: 13px;
}

.time-separator {
  margin: 14px 0;
  color: #a2a2a2;
  font-size: 12px;
  text-align: center;
}

.message-row {
  align-items: flex-start;
  gap: 10px;
  margin: 8px 0;
}

.message-row.outgoing {
  justify-content: flex-end;
}

.message-avatar {
  flex: 0 0 auto;
  width: 34px;
  height: 34px;
  background: #d1d5db;
  color: #374151;
  font-size: 12px;
}

.message-avatar.self {
  background: #111827;
  color: #fff;
}

.bubble-wrap {
  max-width: min(560px, 68%);
}

.sender-name {
  margin: 0 0 4px 2px;
  color: #858585;
  font-size: 12px;
}

.message-bubble {
  position: relative;
  padding: 9px 11px;
  border-radius: 4px;
  background: #fff;
  color: #111827;
  font-size: 14px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.message-bubble::before {
  position: absolute;
  top: 10px;
  left: -5px;
  width: 10px;
  height: 10px;
  background: inherit;
  content: '';
  transform: rotate(45deg);
}

.app-message-card {
  display: flex;
  width: min(360px, 100%);
  min-height: 76px;
  flex-direction: column;
  align-items: stretch;
  gap: 6px;
  padding: 10px 12px;
  border: 0;
  border-radius: 4px;
  background: #fff;
  color: #111827;
  cursor: default;
  line-height: 1.45;
  text-align: left;
  white-space: normal;
}

.app-message-card.clickable {
  cursor: pointer;
}

.app-message-card strong {
  overflow: hidden;
  font-size: 14px;
  font-weight: 600;
  text-overflow: ellipsis;
}

.app-message-card span {
  display: -webkit-box;
  overflow: hidden;
  color: #6b7280;
  font-size: 12px;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.app-message-card em {
  margin-top: auto;
  color: #9ca3af;
  font-size: 12px;
  font-style: normal;
}

.app-message-card.clickable:hover {
  background: #f9fafb;
}

.quote-preview {
  max-width: 320px;
  margin-top: 7px;
  padding: 7px 9px;
  overflow: hidden;
  border-radius: 3px;
  background: #ededed;
  color: #6b7280;
  font-size: 12px;
  line-height: 1.45;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.message-row.outgoing .quote-preview {
  background: rgba(255, 255, 255, 0.45);
}

.inline-media-list {
  display: grid;
  gap: 6px;
  justify-items: start;
}

.inline-media {
  display: block;
  max-width: min(320px, 100%);
  padding: 0;
  overflow: hidden;
  border: 0;
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
}

.inline-media img,
.inline-media video {
  display: block;
  max-width: 100%;
  max-height: 360px;
  border-radius: 4px;
  object-fit: contain;
}

.inline-media span {
  display: inline-block;
  padding: 9px 11px;
  border-radius: 4px;
  background: #fff;
  color: #374151;
  font-size: 13px;
}

.resource-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}

.resource-button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 260px;
  padding: 4px 7px;
  border: 1px solid #d6d6d6;
  border-radius: 4px;
  background: #fff;
  color: #374151;
  font-size: 12px;
  cursor: pointer;
}

.resource-button span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.resource-button em {
  flex: 0 0 auto;
  color: #8a8f98;
  font-style: normal;
}

.resource-button:hover {
  border-color: #9ca3af;
  background: #f9fafb;
}

.message-row.outgoing .message-bubble {
  background: #95ec69;
}

.message-row.outgoing .inline-media-list {
  justify-items: end;
}

.image-preview {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: grid;
  place-items: center;
  padding: 48px;
  background: rgba(0, 0, 0, 0.78);
}

.image-preview img {
  max-width: 94vw;
  max-height: 88vh;
  object-fit: contain;
}

.image-preview-close,
.image-preview-download {
  position: fixed;
  border: 0;
  background: rgba(255, 255, 255, 0.16);
  color: #fff;
  cursor: pointer;
}

.image-preview-close {
  top: 18px;
  right: 24px;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  font-size: 26px;
  line-height: 34px;
}

.image-preview-download {
  right: 24px;
  bottom: 24px;
  padding: 8px 13px;
  border-radius: 4px;
  font-size: 13px;
}

.image-preview-close:hover,
.image-preview-download:hover {
  background: rgba(255, 255, 255, 0.26);
}

.message-row.outgoing .message-bubble::before {
  right: -5px;
  left: auto;
}

.chat-footer {
  justify-content: space-between;
  gap: 12px;
  padding: 9px 18px;
  border-top: 1px solid #d8d8d8;
  background: #f5f5f5;
}

.chat-footer > span {
  color: #8a8f98;
  font-size: 12px;
}

@media (max-width: 980px) {
  .wechat-db-page {
    grid-template-columns: 1fr;
    height: auto;
    overflow: visible;
  }

  .conversation-sidebar {
    border-right: 0;
    border-bottom: 1px solid #d6d6d6;
  }

  .conversation-list {
    height: 280px;
  }

  .chat-shell {
    min-height: 680px;
  }

  .chat-header,
  .chat-footer {
    align-items: stretch;
    flex-direction: column;
    height: auto;
    padding: 12px;
  }

  .chat-tools {
    flex-wrap: wrap;
  }

  .message-search,
  .device-select,
  .type-select {
    width: 100%;
  }

  .bubble-wrap {
    max-width: calc(100% - 48px);
  }
}
</style>
