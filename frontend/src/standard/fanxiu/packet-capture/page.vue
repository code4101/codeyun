<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Connection, QuestionFilled, SetUp } from '@element-plus/icons-vue'

import {
  clearFanxiuPacketActivity,
  decodeFanxiuTcpCapture,
  getFanxiuPacketActivityHistory,
  getFanxiuPacketActivityStream,
  getFanxiuPacketActivityStatus,
  getFanxiuPacketCaptureSnapshot,
  getFanxiuPacketCaptureSessionStatus,
  getFanxiuPacketProxyTimeline,
  listFanxiuTcpBusinessEntries,
  listFanxiuTcpCaptures,
  startFanxiuPacketActivity,
  startFanxiuPacketCaptureSession,
  stopFanxiuPacketActivity,
  stopFanxiuPacketCaptureSession,
  type FanxiuAndroidProxyStatus,
  type FanxiuPacketActivityFlow,
  type FanxiuPacketActivityPayloadEvent,
  type FanxiuPacketActivityStreamDirection,
  type FanxiuPacketActivityStreamResponse,
  type FanxiuPacketActivityStatus,
  type FanxiuPacketPayloadDirection,
  type FanxiuPacketCaptureConnection,
  type FanxiuPacketCaptureSessionStatus,
  type FanxiuPacketCaptureSnapshot,
  type FanxiuPacketProxyEvent,
  type FanxiuPacketProxyStatus,
  type FanxiuTcpBusinessEntry,
  type FanxiuTcpCaptureFile,
  type FanxiuTcpDecodeResponse,
} from '@/api/fanxiu'
import {
  FANXIU_PACKET_PROTOCOL_VISIBILITY_EVENT,
  getHiddenFanxiuPacketProtocols,
} from '../packetProtocolVisibility'

const DEFAULT_DNS_HOSTS = 'cdn-frxxz.akbing.com\nakbing.com'
type PageMode = 'decoder' | 'activity' | 'connections' | 'http'
type ConnectionMeta = {
  firstSeenAt: string
  lastSeenAt: string
  seenCount: number
}
type PacketActivityBaseline = Record<string, { bytes: number; packets: number }>
type PacketActivityRow = {
  key: string
  protocol: string
  remoteLabel: string
  signalLabel: string
  signalScore: number
  signalReason: string
  flow: FanxiuPacketActivityFlow | null
  connection: FanxiuPacketCaptureConnection | null
}
type PayloadJsonFragment = {
  label: string
  text: string
}

const activeMode = ref<PageMode>('decoder')
const dnsHostText = ref(DEFAULT_DNS_HOSTS)
const snapshot = ref<FanxiuPacketCaptureSnapshot | null>(null)
const packetActivityStatus = ref<FanxiuPacketActivityStatus | null>(null)
const proxyStatus = ref<FanxiuPacketProxyStatus | null>(null)
const androidProxyStatus = ref<FanxiuAndroidProxyStatus | null>(null)
const proxyEvents = ref<FanxiuPacketProxyEvent[]>([])
const tcpCaptures = ref<FanxiuTcpCaptureFile[]>([])
const selectedTcpCapture = ref('')
const tcpStream = ref(34)
const tcpServerHost = ref('1.12.44.63')
const tcpDecodeResult = ref<FanxiuTcpDecodeResponse | null>(null)
const tcpDecoderLoading = ref(false)
const tcpBusinessEntries = ref<FanxiuTcpBusinessEntry[]>([])
const tcpBusinessEntryTotal = ref(0)
const tcpBusinessEntryPage = ref(1)
const tcpBusinessEntryPageSize = ref(50)
const tcpBusinessEntryLoading = ref(false)
const selectedTcpBusinessEntryId = ref('')
const hiddenTcpBusinessProtocols = ref<string[]>(getHiddenFanxiuPacketProtocols())
const liveTcpDecodeSize = ref(0)
const liveTcpDecodeRunning = ref(false)
const connectionMetas = ref<Record<string, ConnectionMeta>>({})
const packetActivityBaseline = ref<PacketActivityBaseline | null>(null)
const packetActivityMarkedAt = ref('')
const packetActivityGuidePanels = ref<string[]>([])
const showBackgroundPacketActivity = ref(false)
const selectedPacketActivityKey = ref('')
const packetActivityHistoryItems = ref<FanxiuPacketActivityPayloadEvent[]>([])
const packetActivityHistoryTotal = ref(0)
const packetActivityHistoryPage = ref(1)
const packetActivityHistoryPageSize = ref(20)
const packetActivityHistoryLoading = ref(false)
const packetActivityStream = ref<FanxiuPacketActivityStreamResponse | null>(null)
const packetActivityStreamLoading = ref(false)
const baselineKeys = ref<Set<string> | null>(null)
const operationBaselineKeys = ref<Set<string> | null>(null)
const operationMarkedAt = ref('')
const connectionObserveRunning = ref(true)
const loading = ref(false)
const packetActivityLoading = ref(false)
const sessionLoading = ref(false)
const activeFilter = ref<'all' | 'mumu' | 'fake' | 'proxy' | 'new'>('all')
const includeLowValueEvents = ref(false)
const proxyHost = ref('0.0.0.0')
const proxyPort = ref(8899)
const detailPanels = ref<string[]>([])
const payloadRawPanels = ref<string[]>([])
const liveRawPanels = ref<string[]>([])
const eventPage = ref(1)
const eventPageSize = ref(50)
const eventTotal = ref(0)
const eventSummary = ref<Record<string, number>>({})
const selectedProxyEventKey = ref('')
let connectionRefreshTimer: number | undefined
let proxyRefreshTimer: number | undefined

const dnsHosts = computed(() => {
  const seen = new Set<string>()
  return dnsHostText.value
    .split(/[\n,，\s]+/)
    .map(item => item.trim().toLowerCase())
    .filter((item) => {
      if (!item || seen.has(item)) return false
      seen.add(item)
      return true
    })
})

const summaryItems = computed(() => {
  const summary = snapshot.value?.summary ?? {}
  return [
    { label: '进程', value: summary.process_count ?? 0 },
    { label: '连接', value: summary.connection_count ?? 0 },
    { label: 'MuMu', value: summary.mumu_connection_count ?? 0 },
    { label: 'Fake IP', value: summary.fake_ip_connection_count ?? 0 },
    { label: '已映射', value: summary.mapped_connection_count ?? 0 },
  ]
})

const selectedTcpBusinessEntry = computed(() => {
  if (!tcpBusinessEntries.value.length) return null
  return tcpBusinessEntries.value.find(item => item.id === selectedTcpBusinessEntryId.value) ?? tcpBusinessEntries.value[0]
})

const selectedTcpBusinessEntryJson = computed(() => {
  const entry = selectedTcpBusinessEntry.value
  return entry ? JSON.stringify(entry.content, null, 2) : ''
})

const connectionKey = (item: FanxiuPacketCaptureConnection) => {
  const local = item.local?.label ?? ''
  const remote = item.remote?.label ?? ''
  return `${item.protocol}|${item.pid}|${local}|${remote}`
}

const currentConnectionKeys = computed(() => new Set((snapshot.value?.connections ?? []).map(connectionKey)))

const newConnectionCount = computed(() => {
  if (!baselineKeys.value) return 0
  let count = 0
  for (const key of currentConnectionKeys.value) {
    if (!baselineKeys.value.has(key)) count += 1
  }
  return count
})

const localDateTimeLabel = () => {
  const value = new Date()
  const pad = (item: number) => String(item).padStart(2, '0')
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())} ${pad(value.getHours())}:${pad(value.getMinutes())}:${pad(value.getSeconds())}`
}

const formatClockTime = (value: string) => {
  if (!value) return '-'
  return value.slice(11, 19) || value
}

const parseDateTimeParts = (value: string) => {
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/)
  if (!match) return null
  return {
    year: Number(match[1]),
    month: Number(match[2]),
    day: Number(match[3]),
    time: `${match[4]}:${match[5]}:${match[6]}`,
    monthDay: `${match[2]}-${match[3]}`,
    full: `${match[1]}-${match[2]}-${match[3]} ${match[4]}:${match[5]}:${match[6]}`,
  }
}

const formatTcpBusinessTime = (value: string) => {
  const parts = parseDateTimeParts(value)
  if (!parts) return value || '-'
  const now = new Date()
  const currentYear = now.getFullYear()
  const currentMonth = now.getMonth() + 1
  const currentDay = now.getDate()
  if (parts.year === currentYear && parts.month === currentMonth && parts.day === currentDay) {
    return parts.time
  }
  if (parts.year === currentYear) {
    return `${parts.monthDay} ${parts.time}`
  }
  return parts.full
}

const connectionMetaFor = (item: FanxiuPacketCaptureConnection) => {
  return connectionMetas.value[connectionKey(item)]
}

const updateConnectionMetas = (items: FanxiuPacketCaptureConnection[]) => {
  const now = localDateTimeLabel()
  const next = { ...connectionMetas.value }
  for (const item of items) {
    const key = connectionKey(item)
    const current = next[key]
    if (current) {
      next[key] = { ...current, lastSeenAt: now, seenCount: current.seenCount + 1 }
    } else {
      next[key] = { firstSeenAt: now, lastSeenAt: now, seenCount: 1 }
    }
  }
  connectionMetas.value = next
}

const isNewAfterOperationMark = (item: FanxiuPacketCaptureConnection) => {
  return Boolean(operationBaselineKeys.value && !operationBaselineKeys.value.has(connectionKey(item)))
}

const connectionCandidateRows = computed(() => {
  const items = snapshot.value?.connections ?? []
  const mumuRows = items.filter(item => item.remote && item.process_group === 'mumu')
  const primaryRows = mumuRows.filter(item => item.signal_score >= 45)
  const rows = primaryRows.length ? primaryRows : mumuRows.filter(item => item.signal_score >= 25)
  return [...rows].sort((left, right) => {
    const leftNew = isNewAfterOperationMark(left) ? 1 : 0
    const rightNew = isNewAfterOperationMark(right) ? 1 : 0
    if (leftNew !== rightNew) return rightNew - leftNew
    if (right.signal_score !== left.signal_score) return right.signal_score - left.signal_score
    return String(left.remote?.label ?? '').localeCompare(String(right.remote?.label ?? ''))
  })
})

const packetFlowKeyForConnection = (item: FanxiuPacketCaptureConnection) => {
  if (!item.remote) return ''
  return `${item.protocol}|${item.remote.ip}|${item.remote.port}`
}

const packetActivityFlowMap = computed(() => {
  const map = new Map<string, FanxiuPacketActivityFlow>()
  for (const item of packetActivityStatus.value?.items ?? []) {
    map.set(item.key, item)
  }
  return map
})

const packetFlowTotalBytes = (item: FanxiuPacketActivityFlow | null) => {
  if (!item) return 0
  return item.bytes_up + item.bytes_down
}

const packetFlowTotalPackets = (item: FanxiuPacketActivityFlow | null) => {
  if (!item) return 0
  return item.packets_up + item.packets_down
}

const packetActivityDelta = (key: string, flow: FanxiuPacketActivityFlow | null) => {
  if (!flow) return { bytes: 0, packets: 0 }
  const baseline = packetActivityBaseline.value?.[key]
  if (!baseline) {
    return {
      bytes: packetFlowTotalBytes(flow),
      packets: packetFlowTotalPackets(flow),
    }
  }
  return {
    bytes: Math.max(0, packetFlowTotalBytes(flow) - baseline.bytes),
    packets: Math.max(0, packetFlowTotalPackets(flow) - baseline.packets),
  }
}

const sortPacketActivityRows = (rows: PacketActivityRow[]) => {
  return [...rows].sort((left, right) => {
    const leftDelta = packetActivityDelta(left.key, left.flow)
    const rightDelta = packetActivityDelta(right.key, right.flow)
    if (rightDelta.bytes !== leftDelta.bytes) return rightDelta.bytes - leftDelta.bytes
    const rightBytes = packetFlowTotalBytes(right.flow)
    const leftBytes = packetFlowTotalBytes(left.flow)
    if (rightBytes !== leftBytes) return rightBytes - leftBytes
    return right.signalScore - left.signalScore
  })
}

const packetActivityCandidateRows = computed<PacketActivityRow[]>(() => {
  const rows: PacketActivityRow[] = []
  for (const connection of connectionCandidateRows.value) {
    const key = packetFlowKeyForConnection(connection)
    if (!key) continue
    const flow = packetActivityFlowMap.value.get(key) ?? null
    if (!flow && connection.signal_score < 45) continue
    rows.push({
      key,
      protocol: connection.protocol,
      remoteLabel: connection.remote?.label ?? '-',
      signalLabel: connection.signal_label || '疑似连接',
      signalScore: connection.signal_score,
      signalReason: connection.signal_reason || '-',
      flow,
      connection,
    })
  }
  return sortPacketActivityRows(rows)
})

const packetActivityBackgroundRows = computed<PacketActivityRow[]>(() => {
  const rows: PacketActivityRow[] = []
  const seen = new Set(packetActivityCandidateRows.value.map(item => item.key))
  for (const flow of packetActivityStatus.value?.items ?? []) {
    if (seen.has(flow.key) || packetFlowTotalBytes(flow) <= 0) continue
    rows.push({
      key: flow.key,
      protocol: flow.protocol,
      remoteLabel: flow.remote.label,
      signalLabel: '包活动',
      signalScore: 40,
      signalReason: '抓到包活动，但未在当前 MuMu 连接定位中匹配',
      flow,
      connection: null,
    })
    seen.add(flow.key)
  }
  return sortPacketActivityRows(rows)
})

const packetActivityRows = computed<PacketActivityRow[]>(() => {
  const rows = showBackgroundPacketActivity.value
    ? [...packetActivityCandidateRows.value, ...packetActivityBackgroundRows.value]
    : packetActivityCandidateRows.value
  return sortPacketActivityRows(rows).slice(0, 50)
})

const focusedPacketActivityRow = computed(() => {
  if (!selectedPacketActivityKey.value) return null
  return packetActivityRows.value.find(item => item.key === selectedPacketActivityKey.value) ?? null
})

const packetActivityChangedCount = computed(() => {
  return packetActivityCandidateRows.value.filter((item) => packetActivityDelta(item.key, item.flow).bytes > 0).length
})

const packetActivityPayloadCount = computed(() => {
  return packetActivityCandidateRows.value.filter((item) => {
    const preview = item.flow?.payload_preview
    return Boolean((preview?.up?.length ?? 0) + (preview?.down?.length ?? 0))
  }).length
})

const packetActivityHiddenBackgroundCount = computed(() => {
  return showBackgroundPacketActivity.value ? 0 : packetActivityBackgroundRows.value.length
})

const packetActivitySummary = computed(() => {
  const status = packetActivityStatus.value
  const parts = [
    status?.running ? '采样中' : '未启动',
    `匹配 ${packetActivityCandidateRows.value.length}`,
  ]
  if (packetActivityBaseline.value) parts.push(`操作后变化 ${packetActivityChangedCount.value}`)
  if (packetActivityPayloadCount.value) parts.push(`Payload ${packetActivityPayloadCount.value}`)
  if (showBackgroundPacketActivity.value) parts.push(`背景 ${packetActivityBackgroundRows.value.length}`)
  if (status?.bind_ip) parts.push(`网卡 ${status.bind_ip}`)
  return parts.join(' · ')
})

const packetActivityButtonLabel = computed(() => packetActivityStatus.value?.running ? '包活动采样中' : '开始包活动')

const operationNewCandidateCount = computed(() => {
  if (!operationBaselineKeys.value) return 0
  return connectionCandidateRows.value.filter(item => isNewAfterOperationMark(item)).length
})

const connectionObserverSummary = computed(() => {
  const summary = snapshot.value?.summary ?? {}
  const parts = [
    `疑似 ${connectionCandidateRows.value.length}`,
    `TCP ${summary.mumu_tcp_connection_count ?? 0}`,
    `UDP ${summary.mumu_udp_connection_count ?? 0}`,
  ]
  if (operationBaselineKeys.value) parts.splice(1, 0, `新增 ${operationNewCandidateCount.value}`)
  return parts.join(' · ')
})

const connectionObserveLabel = computed(() => connectionObserveRunning.value ? '观察中' : '开始观察')

const connectionSignalTagType = (item: FanxiuPacketCaptureConnection) => {
  if (item.signal_score >= 70) return 'success'
  if (item.signal_score >= 45) return 'warning'
  return 'info'
}

const connectionScopeLabel = (item: FanxiuPacketCaptureConnection) => {
  if (item.remote_scope === 'fake_ip') return 'Fake IP'
  if (item.remote_scope === 'public') return '公网'
  if (item.remote_scope === 'private') return '内网'
  if (item.remote_scope === 'loopback') return '本机'
  return item.remote_scope || '-'
}

const connectionSeenLabel = (item: FanxiuPacketCaptureConnection) => {
  const meta = connectionMetaFor(item)
  if (!meta) return '-'
  return `${formatClockTime(meta.firstSeenAt)} / ${meta.seenCount}`
}

const filteredConnections = computed(() => {
  const items = snapshot.value?.connections ?? []
  return items.filter((item) => {
    if (activeFilter.value === 'mumu') return item.process_group === 'mumu'
    if (activeFilter.value === 'fake') return item.is_fake_ip
    if (activeFilter.value === 'proxy') return item.process_group === 'proxy'
    if (activeFilter.value === 'new') return baselineKeys.value ? !baselineKeys.value.has(connectionKey(item)) : false
    return true
  })
})

const filteredProxyEvents = computed(() => proxyEvents.value)

const eventKey = (item: FanxiuPacketProxyEvent) => {
  return item.timeline_id || `${item.source || 'event'}:${item.id}:${item.started_at}`
}

const focusedProxyEvent = computed(() => {
  const items = filteredProxyEvents.value
  if (!items.length) return null
  return items.find(item => eventKey(item) === selectedProxyEventKey.value) ?? items[0] ?? null
})

const proxyCandidateCount = computed(() => eventSummary.value.candidate_count ?? 0)
const proxyReadableCount = computed(() => eventSummary.value.readable_count ?? 0)
const proxyResourceCount = computed(() => eventSummary.value.resource_count ?? 0)
const proxyTunnelCount = computed(() => eventSummary.value.tunnel_count ?? 0)
const proxyEventCount = computed(() => eventSummary.value.event_count ?? eventTotal.value)
const proxyLowValueCount = computed(() => proxyResourceCount.value + proxyTunnelCount.value)
const eventHeaderSummary = computed(() => {
  const parts = [`列表 ${eventTotal.value}`]
  if (proxyEventCount.value !== eventTotal.value) parts.push(`全部 ${proxyEventCount.value}`)
  if (proxyCandidateCount.value) parts.push(`疑似接口 ${proxyCandidateCount.value}`)
  if (proxyReadableCount.value) parts.push(`可读 ${proxyReadableCount.value}`)
  if (includeLowValueEvents.value && proxyLowValueCount.value) parts.push(`资源/TLS ${proxyLowValueCount.value}`)
  return parts.join(' · ')
})
const timelineEventFilter = computed(() => includeLowValueEvents.value ? 'all' : 'readable')

const recommendedProxyAddress = computed(() => {
  const addresses = proxyStatus.value?.addresses ?? []
  const usable = addresses.find(address => !address.startsWith('127.') && !address.startsWith('198.18.'))
  return usable || addresses[0] || `${proxyHost.value}:${proxyPort.value}`
})

const captureSessionActive = computed(() => Boolean(proxyStatus.value?.running && androidProxyStatus.value?.matches_target))

const captureSwitchOn = computed(() => Boolean(proxyStatus.value?.running || androidProxyStatus.value?.enabled))

const captureNeedsRestore = computed(() => Boolean(!proxyStatus.value?.running && androidProxyStatus.value?.enabled))

const captureButtonLabel = computed(() => {
  if (captureSessionActive.value) return '抓包已开启'
  if (captureNeedsRestore.value) return '恢复抓包'
  return '开启抓包'
})

const captureButtonType = computed(() => {
  return captureSessionActive.value ? 'success' : 'info'
})

const androidProxyLabel = computed(() => {
  if (!androidProxyStatus.value) return '未检测'
  if (androidProxyStatus.value.last_error) return '异常'
  if (!androidProxyStatus.value.available) return '未连接'
  if (!androidProxyStatus.value.enabled) return '已清空'
  if (androidProxyStatus.value.matches_target) return '已接入'
  return '指向其他代理'
})

const proxyEventEmptyText = computed(() => includeLowValueEvents.value ? '暂无事件' : '暂无可读事件')

const captureDiagnosticText = computed(() => {
  if (captureSessionActive.value) return 'Python 代理运行中；安卓代理已指向此地址'
  const pythonText = `Python ${proxyStatus.value?.running ? '运行中' : '未运行'}`
  if (androidProxyStatus.value?.http_proxy) return `${pythonText}；安卓当前 ${androidProxyStatus.value.http_proxy}`
  return `${pythonText}；安卓 ${androidProxyLabel.value}`
})

const proxyEmptyStateText = computed(() => {
  if (captureSessionActive.value) return '等待 HTTP/HTTPS'
  if (captureSwitchOn.value) return '会话未就绪'
  return '抓包未开启'
})

const mappedHostLabel = (item: FanxiuPacketCaptureConnection) => {
  return item.mapped_hosts.length ? item.mapped_hosts.join(', ') : '-'
}

const connectionTagType = (item: FanxiuPacketCaptureConnection) => {
  if (item.process_group === 'mumu') return 'primary'
  if (item.process_group === 'proxy') return 'success'
  return 'info'
}

const isNewConnection = (item: FanxiuPacketCaptureConnection) => {
  return Boolean(baselineKeys.value && !baselineKeys.value.has(connectionKey(item)))
}

const eventTypeLabel = (item: FanxiuPacketProxyEvent) => {
  if (item.event_type === 'plain_http') return 'HTTP'
  if (item.event_type === 'tls_tunnel') return 'TLS'
  if (item.event_type === 'error') return '错误'
  return item.event_type || '-'
}

const signalTagType = (item: FanxiuPacketProxyEvent) => {
  if (item.error || item.semantic_role === 'error') return 'danger'
  if (item.semantic_role === 'api_candidate') return 'success'
  if (item.semantic_role === 'readable_http') return 'warning'
  if (item.semantic_role === 'static_resource' || item.semantic_role === 'tls_tunnel') return 'info'
  return 'warning'
}

const formatBytes = (value: number) => {
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(2)} MB`
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${value} B`
}

const packetActivityDeltaLabel = (row: PacketActivityRow) => {
  const delta = packetActivityDelta(row.key, row.flow)
  if (!packetActivityBaseline.value) return '-'
  return `${formatBytes(delta.bytes)} / ${delta.packets} 包`
}

const packetActivityTotalLabel = (row: PacketActivityRow) => {
  if (!row.flow) return '-'
  return `${formatBytes(packetFlowTotalBytes(row.flow))} / ${packetFlowTotalPackets(row.flow)} 包`
}

const packetActivityDirectionLabel = (row: PacketActivityRow) => {
  if (!row.flow) return '-'
  return `↑${formatBytes(row.flow.bytes_up)} ↓${formatBytes(row.flow.bytes_down)}`
}

const compactPayloadText = (value: string) => {
  return (value || '').replace(/\s+/g, ' ').trim()
}

const payloadDisplayText = (side: FanxiuPacketPayloadDirection | undefined) => {
  if (!side?.length) return ''
  return side.text || side.ascii || side.hex
}

const payloadStreamText = (side: FanxiuPacketActivityStreamDirection | undefined) => {
  if (!side?.preview?.length) return ''
  return payloadDisplayText(side.preview)
}

const payloadStreamMeta = (side: FanxiuPacketActivityStreamDirection | undefined) => {
  if (!side?.packet_count) return '无 payload'
  const parts = [
    `${side.packet_count} 包`,
    `${formatBytes(side.payload_bytes)}`,
  ]
  if (side.dropped_bytes > 0) parts.push(`显示最近 ${formatBytes(side.preview.length)}`)
  if (side.truncated_packets > 0) parts.push(`${side.truncated_packets} 包截断`)
  return parts.join(' · ')
}

const normalizeNestedJsonValue = (value: unknown, depth = 0): unknown => {
  if (depth > 3) return value
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (looksLikeJson(trimmed)) {
      try {
        return normalizeNestedJsonValue(JSON.parse(trimmed), depth + 1)
      } catch {
        return value
      }
    }
    return value
  }
  if (Array.isArray(value)) {
    return value.map(item => normalizeNestedJsonValue(item, depth + 1))
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, item]) => [
        key,
        normalizeNestedJsonValue(item, depth + 1),
      ]),
    )
  }
  return value
}

const findBalancedJsonEnd = (value: string, start: number) => {
  const stack: string[] = []
  let inString = false
  let escaped = false
  for (let index = start; index < value.length; index += 1) {
    const char = value[index]
    if (inString) {
      if (escaped) {
        escaped = false
      } else if (char === '\\') {
        escaped = true
      } else if (char === '"') {
        inString = false
      }
      continue
    }
    if (char === '"') {
      inString = true
      continue
    }
    if (char === '{') {
      stack.push('}')
    } else if (char === '[') {
      stack.push(']')
    } else if (char === '}' || char === ']') {
      if (stack.pop() !== char) return -1
      if (!stack.length) return index
    }
  }
  return -1
}

const payloadJsonFragments = (value: string, limit = 4): PayloadJsonFragment[] => {
  const source = value || ''
  const fragments: PayloadJsonFragment[] = []
  const seen = new Set<string>()
  for (let index = 0; index < source.length && fragments.length < limit; index += 1) {
    const char = source[index]
    if (char !== '{' && char !== '[') continue
    const end = findBalancedJsonEnd(source, index)
    if (end <= index) continue
    const raw = source.slice(index, end + 1)
    try {
      const parsed = normalizeNestedJsonValue(JSON.parse(raw))
      const formatted = JSON.stringify(parsed, null, 2)
      if (!seen.has(formatted)) {
        fragments.push({ label: `JSON ${fragments.length + 1}`, text: formatted })
        seen.add(formatted)
      }
      index = end
    } catch {
      continue
    }
  }
  return fragments
}

const payloadStreamSides = computed(() => [
  { name: 'up', label: '上行', data: packetActivityStream.value?.up },
  { name: 'down', label: '下行', data: packetActivityStream.value?.down },
])

const payloadRawSummary = computed(() => {
  return payloadStreamSides.value
    .map(side => `${side.label} ${payloadStreamMeta(side.data)}`)
    .join(' · ')
})

const payloadStreamJsonFragments = computed(() => {
  const fragments: PayloadJsonFragment[] = []
  for (const side of payloadStreamSides.value) {
    fragments.push(
      ...payloadJsonFragments(payloadStreamText(side.data)).map(fragment => ({
        label: `${side.label} ${fragment.label}`,
        text: fragment.text,
      })),
    )
  }
  return fragments
})

const packetPayloadSidePreview = (
  side: FanxiuPacketPayloadDirection | undefined,
  prefix: string,
) => {
  if (!side?.length) return ''
  const ascii = compactPayloadText(side.text || side.ascii)
  const sample = ascii && side.printable_ratio >= 0.35 ? ascii : side.hex
  return `${prefix}${side.guess} ${sample}`.slice(0, 180)
}

const packetPayloadPreview = (row: PacketActivityRow) => {
  const preview = row.flow?.payload_preview
  if (!preview) return '无 payload'
  const parts = [
    packetPayloadSidePreview(preview.up, '↑'),
    packetPayloadSidePreview(preview.down, '↓'),
  ].filter(Boolean)
  return parts.length ? parts.join(' | ') : '无 payload'
}

const payloadTagType = (guess: string) => {
  if (guess.includes('HTTP') || guess.includes('JSON') || guess.includes('可读')) return 'success'
  if (guess.includes('混合')) return 'warning'
  if (guess.includes('无负载')) return 'info'
  return 'danger'
}

const payloadDirectionLabel = (direction: string) => direction === 'up' ? '上行' : '下行'

const queryParamsFromPayloadText = (text: string) => {
  const value = (text || '').trim()
  if (!value) return ''
  const requestLine = value.split(/\r?\n/)[0] || value
  const requestMatch = requestLine.match(/^(GET|POST|PUT|DELETE|PATCH|HEAD)\s+(\S+)/i)
  const rawTarget = requestMatch?.[2] || (value.match(/https?:\/\/\S+/i)?.[0] ?? '')
  if (!rawTarget || !rawTarget.includes('?')) return ''
  const query = rawTarget.split('?').slice(1).join('?').split('#')[0]
  const params = new URLSearchParams(query)
  const result: Record<string, string> = {}
  params.forEach((item, key) => {
    result[key] = item
  })
  return Object.keys(result).length ? JSON.stringify(result, null, 2) : ''
}

const payloadHttpLine = (side: FanxiuPacketPayloadDirection | undefined) => {
  const text = payloadDisplayText(side)
  return text.split(/\r?\n/).find(line => /^(GET|POST|PUT|DELETE|PATCH|HEAD|HTTP\/)/i.test(line.trim())) || ''
}

const payloadHistoryPreview = (item: FanxiuPacketActivityPayloadEvent) => {
  const text = compactPayloadText(payloadDisplayText(item.payload_preview))
  return text || item.payload_preview.hex || '-'
}

const packetActivityTagType = (row: PacketActivityRow) => {
  const delta = packetActivityDelta(row.key, row.flow)
  if (packetActivityBaseline.value && delta.bytes > 0) return 'danger'
  if (row.signalScore >= 70) return 'success'
  if (row.signalScore >= 45) return 'warning'
  return 'info'
}

const eventBodyPreview = (value: string) => {
  const trimmed = (value || '').trim()
  return trimmed || '-'
}

const looksLikeJson = (value: string) => {
  const trimmed = (value || '').trim()
  return trimmed.startsWith('{') || trimmed.startsWith('[')
}

const formatJsonText = (value: string) => {
  const trimmed = (value || '').trim()
  if (!trimmed) return ''
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2)
  } catch {
    return trimmed
  }
}

const jsonBlocksForEvent = (item: FanxiuPacketProxyEvent | null) => {
  if (!item) return []
  const blocks: Array<{ label: string; text: string }> = []
  if (looksLikeJson(item.response_body_text)) {
    blocks.push({ label: '响应 JSON', text: formatJsonText(item.response_body_text) })
  }
  if (looksLikeJson(item.request_body_text)) {
    blocks.push({ label: '请求 JSON', text: formatJsonText(item.request_body_text) })
  }
  return blocks
}

const focusedProxyJsonBlocks = computed(() => jsonBlocksForEvent(focusedProxyEvent.value))

const jsonPreviewForEvent = (item: FanxiuPacketProxyEvent) => {
  const blocks = jsonBlocksForEvent(item)
  if (!blocks.length) {
    if (item.event_type === 'tls_tunnel') return 'TLS 加密隧道，没有 JSON 明文'
    if (item.semantic_role === 'static_resource') return '资源请求，没有 JSON'
    return '没有 JSON 明文'
  }
  const compact = blocks[0].text.replace(/\s+/g, ' ').trim()
  return compact.length > 260 ? `${compact.slice(0, 260)}...` : compact
}

const selectProxyEvent = (item: FanxiuPacketProxyEvent) => {
  selectedProxyEventKey.value = eventKey(item)
}

const proxyEventRowClass = ({ row }: { row: FanxiuPacketProxyEvent }) => {
  return eventKey(row) === selectedProxyEventKey.value ? 'is-selected-event' : ''
}

const refreshSnapshot = async (resolveDns = false, showLoading = true) => {
  if (showLoading) loading.value = true
  try {
    const data = await getFanxiuPacketCaptureSnapshot(resolveDns ? dnsHosts.value : [], resolveDns)
    snapshot.value = data
    updateConnectionMetas(data.connections)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取凡修抓包快照失败')
  } finally {
    if (showLoading) loading.value = false
  }
}

const refreshPacketActivity = async (showLoading = false) => {
  if (showLoading) packetActivityLoading.value = true
  try {
    packetActivityStatus.value = await getFanxiuPacketActivityStatus()
    if (activeMode.value === 'decoder') {
      await autoDecodeLiveTcpCapture()
    }
    if (selectedPacketActivityKey.value && packetActivityHistoryPage.value === 1) {
      await Promise.all([
        refreshPacketActivityHistory(false),
        refreshPacketActivityStream(false),
      ])
    }
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取包活动失败')
  } finally {
    if (showLoading) packetActivityLoading.value = false
  }
}

const refreshPacketActivityHistory = async (showLoading = false) => {
  if (!selectedPacketActivityKey.value) {
    packetActivityHistoryItems.value = []
    packetActivityHistoryTotal.value = 0
    return
  }
  if (showLoading) packetActivityHistoryLoading.value = true
  try {
    const data = await getFanxiuPacketActivityHistory({
      key: selectedPacketActivityKey.value,
      offset: (packetActivityHistoryPage.value - 1) * packetActivityHistoryPageSize.value,
      limit: packetActivityHistoryPageSize.value,
    })
    packetActivityHistoryItems.value = data.items
    packetActivityHistoryTotal.value = data.total
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取 payload 历史失败')
  } finally {
    if (showLoading) packetActivityHistoryLoading.value = false
  }
}

const refreshPacketActivityStream = async (showLoading = false) => {
  if (!selectedPacketActivityKey.value) {
    packetActivityStream.value = null
    return
  }
  if (showLoading) packetActivityStreamLoading.value = true
  try {
    packetActivityStream.value = await getFanxiuPacketActivityStream({
      key: selectedPacketActivityKey.value,
      max_bytes: 32768,
    })
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取 payload 拼接失败')
  } finally {
    if (showLoading) packetActivityStreamLoading.value = false
  }
}

const selectPacketActivityRow = async (row: PacketActivityRow) => {
  selectedPacketActivityKey.value = row.key
  packetActivityHistoryPage.value = 1
  packetActivityHistoryItems.value = []
  packetActivityHistoryTotal.value = 0
  packetActivityStream.value = null
  await Promise.all([
    refreshPacketActivityHistory(true),
    refreshPacketActivityStream(true),
  ])
}

const packetActivityRowClass = ({ row }: { row: PacketActivityRow }) => {
  return row.key === selectedPacketActivityKey.value ? 'is-selected-event' : ''
}

const handlePacketActivityHistoryPageChange = async () => {
  await refreshPacketActivityHistory(true)
}

const handlePacketActivityHistoryPageSizeChange = async () => {
  packetActivityHistoryPage.value = 1
  await refreshPacketActivityHistory(true)
}

const applyProxyStatus = (status: FanxiuPacketProxyStatus) => {
  proxyStatus.value = status
  if (status.host) proxyHost.value = status.host
  if (status.port) proxyPort.value = status.port
}

const applyCaptureSessionStatus = (status: FanxiuPacketCaptureSessionStatus) => {
  applyProxyStatus(status.proxy)
  androidProxyStatus.value = status.android
}

const refreshCaptureSessionStatus = async () => {
  try {
    applyCaptureSessionStatus(await getFanxiuPacketCaptureSessionStatus())
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取抓包状态失败')
  }
}

const refreshProxyEvents = async () => {
  try {
    const data = await getFanxiuPacketProxyTimeline({
      offset: (eventPage.value - 1) * eventPageSize.value,
      limit: eventPageSize.value,
      event_filter: timelineEventFilter.value,
    })
    applyProxyStatus(data.status)
    if (!data.items.length && data.total > 0 && eventPage.value > 1) {
      eventPage.value = Math.max(1, Math.ceil(data.total / eventPageSize.value))
      await refreshProxyEvents()
      return
    }
    proxyEvents.value = data.items
    eventTotal.value = data.total
    eventSummary.value = data.summary
    if (data.items.length && !data.items.some(item => eventKey(item) === selectedProxyEventKey.value)) {
      selectedProxyEventKey.value = eventKey(data.items[0])
    }
    if (!data.items.length) selectedProxyEventKey.value = ''
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取事件失败')
  }
}

const startCaptureSession = async () => {
  sessionLoading.value = true
  try {
    const status = await startFanxiuPacketCaptureSession(proxyHost.value, proxyPort.value)
    applyCaptureSessionStatus(status)
    await refreshProxyEvents()
    if (status.active) {
      ElMessage.success('已开启抓包并设置安卓代理')
    } else {
      ElMessage.warning(status.last_error || 'Python 代理已启动，但安卓代理没有接上')
    }
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '开启抓包失败')
  } finally {
    sessionLoading.value = false
  }
}

const stopCaptureSession = async () => {
  sessionLoading.value = true
  try {
    const status = await stopFanxiuPacketCaptureSession()
    applyCaptureSessionStatus(status)
    await refreshProxyEvents()
    if (status.proxy.running || status.android.enabled) {
      ElMessage.warning(status.last_error || '关闭未完全完成，请查看顶部状态')
    } else {
      ElMessage.success('已关闭抓包并清理安卓代理')
    }
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '关闭抓包失败')
  } finally {
    sessionLoading.value = false
  }
}

const toggleCaptureSession = () => {
  if (captureSwitchOn.value && !captureNeedsRestore.value) {
    stopCaptureSession()
  } else {
    startCaptureSession()
  }
}

const toggleConnectionObserve = () => {
  connectionObserveRunning.value = !connectionObserveRunning.value
  if (connectionObserveRunning.value) {
    refreshSnapshot(false, true)
  }
}

const togglePacketActivity = async () => {
  packetActivityLoading.value = true
  try {
    const wasRunning = Boolean(packetActivityStatus.value?.running)
    packetActivityStatus.value = packetActivityStatus.value?.running
      ? await stopFanxiuPacketActivity()
      : await startFanxiuPacketActivity()
    if (!wasRunning && packetActivityStatus.value.running) {
      selectedPacketActivityKey.value = ''
      packetActivityHistoryItems.value = []
      packetActivityHistoryTotal.value = 0
      packetActivityHistoryPage.value = 1
      packetActivityStream.value = null
    }
    if (packetActivityStatus.value.last_error) {
      ElMessage.warning(packetActivityStatus.value.last_error)
    }
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '切换包活动失败')
  } finally {
    packetActivityLoading.value = false
  }
}

const markPacketActivity = () => {
  const baseline: PacketActivityBaseline = {}
  for (const item of packetActivityStatus.value?.items ?? []) {
    baseline[item.key] = {
      bytes: packetFlowTotalBytes(item),
      packets: packetFlowTotalPackets(item),
    }
  }
  packetActivityBaseline.value = baseline
  packetActivityMarkedAt.value = localDateTimeLabel()
  ElMessage.success('已标记当前包活动，去游戏里执行一次目标操作')
}

const clearPacketActivityMark = () => {
  packetActivityBaseline.value = null
  packetActivityMarkedAt.value = ''
}

const clearPacketActivity = async () => {
  packetActivityLoading.value = true
  try {
    packetActivityStatus.value = await clearFanxiuPacketActivity()
    clearPacketActivityMark()
    selectedPacketActivityKey.value = ''
    packetActivityHistoryItems.value = []
    packetActivityHistoryTotal.value = 0
    packetActivityHistoryPage.value = 1
    packetActivityStream.value = null
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '清空包活动失败')
  } finally {
    packetActivityLoading.value = false
  }
}

const loadTcpCaptures = async (showLoading = false) => {
  if (showLoading) tcpDecoderLoading.value = true
  try {
    const data = await listFanxiuTcpCaptures()
    tcpCaptures.value = data.items
    if (!selectedTcpCapture.value && data.items.length) {
      selectedTcpCapture.value = data.items[0].relative_path || data.items[0].path
    }
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取 TCP 抓包文件失败')
  } finally {
    if (showLoading) tcpDecoderLoading.value = false
  }
}

const loadTcpBusinessEntries = async (showLoading = false, silent = false) => {
  if (showLoading) tcpBusinessEntryLoading.value = true
  try {
    const data = await listFanxiuTcpBusinessEntries({
      page: tcpBusinessEntryPage.value,
      page_size: tcpBusinessEntryPageSize.value,
      hidden_protocols: hiddenTcpBusinessProtocols.value.join(','),
    })
    tcpBusinessEntries.value = data.items
    tcpBusinessEntryTotal.value = data.total
    if (!data.items.length) {
      selectedTcpBusinessEntryId.value = ''
    } else if (!data.items.some(item => item.id === selectedTcpBusinessEntryId.value)) {
      selectedTcpBusinessEntryId.value = data.items[0].id
    }
  } catch (error: any) {
    if (!silent) ElMessage.error(error?.response?.data?.detail || error?.message || '读取明文结果失败')
  } finally {
    if (showLoading) tcpBusinessEntryLoading.value = false
  }
}

const decodeSelectedTcpCapture = async (options: { persist?: boolean; silent?: boolean } = {}) => {
  const persist = options.persist ?? true
  const silent = options.silent ?? false
  if (!selectedTcpCapture.value) {
    if (!silent) ElMessage.warning('先选择一个 pcapng 文件')
    return
  }
  if (!silent) tcpDecoderLoading.value = true
  try {
    const data = await decodeFanxiuTcpCapture({
      pcap: selectedTcpCapture.value,
      stream: tcpStream.value,
      server_host: tcpServerHost.value,
      persist,
    })
    tcpDecodeResult.value = data
    tcpStream.value = data.stream
    if (tcpBusinessEntryPage.value === 1) {
      await loadTcpBusinessEntries(false, silent)
    }
    if (!silent) {
      await loadTcpCaptures(false)
      ElMessage.success(`已解析 ${data.frames.length} 条明文结果`)
    }
  } catch (error: any) {
    if (!silent) ElMessage.error(error?.response?.data?.detail || error?.message || '解析 TCP 抓包失败')
  } finally {
    if (!silent) tcpDecoderLoading.value = false
  }
}

const autoDecodeLiveTcpCapture = async () => {
  const status = packetActivityStatus.value
  if (!status?.running || !status.pcap_path || liveTcpDecodeRunning.value) return false
  if (status.pcap_size < 256 || status.pcap_size === liveTcpDecodeSize.value) return false
  liveTcpDecodeRunning.value = true
  try {
    selectedTcpCapture.value = status.pcap_path
    tcpStream.value = -1
    liveTcpDecodeSize.value = status.pcap_size
    await decodeSelectedTcpCapture({ persist: false, silent: true })
    return true
  } finally {
    liveTcpDecodeRunning.value = false
  }
}

const startSimpleTcpCapture = async () => {
  packetActivityLoading.value = true
  try {
    const status = await startFanxiuPacketActivity()
    packetActivityStatus.value = status
    tcpDecodeResult.value = null
    liveTcpDecodeSize.value = 0
    if (status.running) {
      ElMessage.success('已开始抓包，现在去游戏里操作一下')
    } else {
      ElMessage.warning(status.last_error || '没有启动成功，可能需要管理员权限')
    }
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '开始抓包失败')
  } finally {
    packetActivityLoading.value = false
  }
}

const stopSimpleTcpCapture = async () => {
  packetActivityLoading.value = true
  try {
    packetActivityStatus.value = await stopFanxiuPacketActivity()
    await loadTcpCaptures(false)
    if (packetActivityStatus.value.pcap_path) {
      selectedTcpCapture.value = packetActivityStatus.value.pcap_path
      tcpStream.value = -1
      ElMessage.success('已停止抓包，可以解析明文')
    } else {
      ElMessage.warning(packetActivityStatus.value.last_error || '已停止，但没有生成 pcap 文件')
    }
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '停止抓包失败')
  } finally {
    packetActivityLoading.value = false
  }
}

const stopAndDecodeSimpleTcpCapture = async () => {
  if (packetActivityStatus.value?.running) {
    await stopSimpleTcpCapture()
  }
  if (!selectedTcpCapture.value && packetActivityStatus.value?.pcap_path) {
    selectedTcpCapture.value = packetActivityStatus.value.pcap_path
  }
  if (!selectedTcpCapture.value) {
    ElMessage.warning('还没有可解析的抓包文件')
    return
  }
  if (selectedTcpCapture.value === packetActivityStatus.value?.pcap_path) {
    tcpStream.value = -1
  }
  tcpBusinessEntryPage.value = 1
  await decodeSelectedTcpCapture()
}

const handleTcpBusinessPageChange = (page: number) => {
  tcpBusinessEntryPage.value = page
  loadTcpBusinessEntries(true)
}

const handleTcpBusinessPageSizeChange = (pageSize: number) => {
  tcpBusinessEntryPageSize.value = pageSize
  tcpBusinessEntryPage.value = 1
  loadTcpBusinessEntries(true)
}

const isTcpBusinessUpstream = (direction: string) => direction === 'c2s'

const selectTcpBusinessEntry = (row: FanxiuTcpBusinessEntry) => {
  selectedTcpBusinessEntryId.value = row.id
}

const tcpBusinessEntryRowClass = ({ row }: { row: FanxiuTcpBusinessEntry }) => {
  return [
    row.id === selectedTcpBusinessEntryId.value ? 'is-selected-event' : '',
    isTcpBusinessUpstream(row.direction) ? 'is-upstream-event' : '',
  ].filter(Boolean).join(' ')
}

const tcpBusinessDisplaySegments = (row: FanxiuTcpBusinessEntry) => {
  if (row.display_segments?.length) return row.display_segments
  return [{ text: row.display_text || JSON.stringify(row.content), kind: 'text' }]
}

const toggleSimpleTcpCapture = async () => {
  if (packetActivityStatus.value?.running) {
    await stopAndDecodeSimpleTcpCapture()
    return
  }
  await startSimpleTcpCapture()
}

const tcpDirectionLabel = (value: string) => value === 'c2s' ? '上行' : '下行'

const markOperation = () => {
  operationBaselineKeys.value = new Set(currentConnectionKeys.value)
  operationMarkedAt.value = localDateTimeLabel()
  ElMessage.success('已标记当前连接，去游戏里执行一次操作后看新增连接')
}

const clearOperationMark = () => {
  operationBaselineKeys.value = null
  operationMarkedAt.value = ''
}

const setBaseline = () => {
  baselineKeys.value = new Set(currentConnectionKeys.value)
  ElMessage.success('已设置当前连接基线')
}

const clearBaseline = () => {
  baselineKeys.value = null
}

const handleEventPageChange = (page: number) => {
  eventPage.value = page
  refreshProxyEvents()
}

const handleEventPageSizeChange = (size: number) => {
  eventPageSize.value = size
  eventPage.value = 1
  refreshProxyEvents()
}

watch(includeLowValueEvents, () => {
  eventPage.value = 1
  selectedProxyEventKey.value = ''
  refreshProxyEvents()
})

const handlePacketProtocolVisibilityChange = () => {
  hiddenTcpBusinessProtocols.value = getHiddenFanxiuPacketProtocols()
  tcpBusinessEntryPage.value = 1
  if (activeMode.value === 'decoder') {
    loadTcpBusinessEntries(false, true)
  }
}

onMounted(() => {
  window.addEventListener(FANXIU_PACKET_PROTOCOL_VISIBILITY_EVENT, handlePacketProtocolVisibilityChange)
  loadTcpCaptures()
  loadTcpBusinessEntries()
  refreshSnapshot(false, true)
  refreshPacketActivity()
  connectionRefreshTimer = window.setInterval(() => {
    if ((activeMode.value === 'connections' || activeMode.value === 'activity') && connectionObserveRunning.value) {
      refreshSnapshot(false, false)
    }
    if (activeMode.value === 'activity' || activeMode.value === 'decoder') {
      refreshPacketActivity()
    }
  }, 2000)
  proxyRefreshTimer = window.setInterval(() => {
    if (activeMode.value === 'http') {
      refreshProxyEvents()
    }
  }, 3000)
})

onUnmounted(() => {
  window.removeEventListener(FANXIU_PACKET_PROTOCOL_VISIBILITY_EVENT, handlePacketProtocolVisibilityChange)
  if (connectionRefreshTimer) window.clearInterval(connectionRefreshTimer)
  if (proxyRefreshTimer) window.clearInterval(proxyRefreshTimer)
})

watch(activeMode, (mode) => {
  if (mode === 'http') {
    refreshCaptureSessionStatus()
    refreshProxyEvents()
  } else if (mode === 'activity') {
    refreshSnapshot(false, true)
    refreshPacketActivity(true)
  } else if (mode === 'decoder') {
    loadTcpCaptures(true)
    loadTcpBusinessEntries(true)
  } else {
    refreshSnapshot(false, true)
  }
})
</script>

<template>
  <div class="fanxiu-packet-capture-page">
    <header class="page-header">
      <div>
        <h2 class="page-title">凡修抓包</h2>
      </div>
    </header>

    <div class="mode-switch">
      <button :class="{ active: activeMode === 'decoder' }" @click="activeMode = 'decoder'">TCP 解析</button>
      <button :class="{ active: activeMode === 'activity' }" @click="activeMode = 'activity'">包活动</button>
      <button :class="{ active: activeMode === 'connections' }" @click="activeMode = 'connections'">连接定位</button>
      <button :class="{ active: activeMode === 'http' }" @click="activeMode = 'http'">HTTP 代理</button>
    </div>

    <section v-if="activeMode === 'decoder'" class="tcp-decoder-panel">
      <div class="tcp-simple-actions">
        <el-button
          size="large"
          :type="packetActivityStatus?.running ? 'success' : 'primary'"
          :loading="packetActivityLoading || tcpDecoderLoading"
          @click="toggleSimpleTcpCapture"
        >
          {{ packetActivityStatus?.running ? '停止抓包' : '开始抓包' }}
        </el-button>
      </div>

      <section class="tcp-plain-results">
        <el-table
          class="tcp-plain-table"
          v-loading="tcpBusinessEntryLoading"
          :data="tcpBusinessEntries"
          table-layout="fixed"
          :fit="true"
          border
          height="44vh"
          :row-class-name="tcpBusinessEntryRowClass"
          empty-text="还没有解析出业务明文"
          @row-click="selectTcpBusinessEntry"
        >
          <el-table-column label="业务包" prop="name" width="190" />
          <el-table-column label="内容" min-width="520">
            <template #default="{ row }">
              <span class="tcp-business-content">
                <span
                  v-for="(segment, index) in tcpBusinessDisplaySegments(row)"
                  :key="`${row.id}-${index}`"
                  :class="{ 'tcp-business-param': segment.kind === 'param' }"
                >{{ segment.text }}</span>
              </span>
            </template>
          </el-table-column>
          <el-table-column label="时间" width="112">
            <template #default="{ row }">{{ formatTcpBusinessTime(row.decoded_at) }}</template>
          </el-table-column>
        </el-table>
        <div class="tcp-business-pagination">
          <el-pagination
            v-model:current-page="tcpBusinessEntryPage"
            v-model:page-size="tcpBusinessEntryPageSize"
            :page-sizes="[50, 100, 200]"
            :total="tcpBusinessEntryTotal"
            layout="total, sizes, prev, pager, next"
            small
            @current-change="handleTcpBusinessPageChange"
            @size-change="handleTcpBusinessPageSizeChange"
          />
        </div>
        <section v-if="selectedTcpBusinessEntry" class="tcp-business-detail json-block">
          <h4>{{ selectedTcpBusinessEntry.name }} · {{ tcpDirectionLabel(selectedTcpBusinessEntry.direction) }} · {{ selectedTcpBusinessEntry.decoded_at }}</h4>
          <pre>{{ selectedTcpBusinessEntryJson }}</pre>
        </section>
      </section>

    </section>

    <section v-else-if="activeMode === 'activity'" class="packet-activity-panel">
      <div class="section-header">
        <div>
          <h3 class="section-title">TCP/UDP 包活动</h3>
          <div class="proxy-status-line">
            <span>{{ packetActivitySummary }}</span>
            <span v-if="packetActivityMarkedAt">标记 {{ formatClockTime(packetActivityMarkedAt) }}</span>
            <span v-if="packetActivityStatus?.started_at">启动 {{ formatClockTime(packetActivityStatus.started_at) }}</span>
          </div>
        </div>
        <div class="event-tools">
          <el-button
            class="observe-toggle-button"
            size="small"
            :type="packetActivityStatus?.running ? 'success' : 'info'"
            :plain="!packetActivityStatus?.running"
            :loading="packetActivityLoading"
            @click="togglePacketActivity"
          >
            {{ packetActivityButtonLabel }}
          </el-button>
          <el-button size="small" :disabled="!packetActivityStatus?.running" @click="markPacketActivity">标记操作</el-button>
          <el-button v-if="packetActivityBaseline" size="small" @click="clearPacketActivityMark">清标记</el-button>
          <el-button size="small" :disabled="!packetActivityStatus?.items?.length" @click="clearPacketActivity">清空</el-button>
          <el-checkbox v-model="showBackgroundPacketActivity" size="small">背景活动</el-checkbox>
          <el-tooltip content="管理员权限下统计 TCP/UDP 包数和字节变化；这里只看活动量，不解析或解密包内容。" placement="top">
            <el-icon class="help-icon"><QuestionFilled /></el-icon>
          </el-tooltip>
        </div>
      </div>

      <el-collapse v-model="packetActivityGuidePanels" class="packet-guide-collapse">
        <el-collapse-item title="操作指南" name="guide">
          <ol class="packet-guide-list">
            <li>点“开始包活动”，让页面保持采样中。</li>
            <li>点“标记操作”，再回游戏执行一次要分析的动作。</li>
            <li>回来优先看“操作后变化”大的“疑似业务连接”，再看 Payload 是否像明文。</li>
            <li>默认隐藏未匹配背景活动；排查漏网连接时再勾选“背景活动”。</li>
            <li>Payload 历史保留当前会话里的样本字节，用于判断明文/二进制，不做解密或协议解析。</li>
          </ol>
        </el-collapse-item>
      </el-collapse>

      <el-alert
        v-if="packetActivityStatus?.last_error"
        class="warning-line"
        type="error"
        :closable="false"
        :title="packetActivityStatus.last_error"
      />

      <div v-if="!packetActivityStatus?.running" class="empty-guide">
        <strong>包活动未启动</strong>
        <span>点击“开始包活动”，再标记一次操作。</span>
      </div>
      <div v-else-if="!packetActivityRows.length" class="empty-guide">
        <strong>暂无包活动</strong>
        <span v-if="packetActivityHiddenBackgroundCount">
          已隐藏 {{ packetActivityHiddenBackgroundCount }} 条背景活动；需要排查时勾选“背景活动”。
        </span>
        <span v-else>保持采样中，然后在游戏里执行一次目标操作。</span>
      </div>

      <el-table
        v-if="packetActivityRows.length"
        class="packet-activity-table"
        :data="packetActivityRows"
        table-layout="fixed"
        :fit="true"
        border
        empty-text="暂无包活动"
        :row-class-name="packetActivityRowClass"
        @row-click="selectPacketActivityRow"
      >
        <el-table-column label="判断" width="160">
          <template #default="{ row }">
            <div class="signal-cell">
              <el-tag :type="packetActivityTagType(row)" effect="plain" size="small">
                {{ row.signalLabel }}
              </el-tag>
              <span v-if="row.signalScore" class="signal-score">{{ row.signalScore }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="连接" min-width="210">
          <template #default="{ row }">
            <span class="connection-endpoint">
              <strong>{{ row.protocol.toUpperCase() }}</strong>
              {{ row.remoteLabel }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作后变化" width="150">
          <template #default="{ row }">
            <span class="compact-cell-text">{{ packetActivityDeltaLabel(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="累计" width="150">
          <template #default="{ row }">
            <span class="compact-cell-text">{{ packetActivityTotalLabel(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="方向" width="132">
          <template #default="{ row }">
            <span class="compact-cell-text">{{ packetActivityDirectionLabel(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="Payload" min-width="420">
          <template #default="{ row }">
            <span class="payload-preview-text">
              {{ packetPayloadPreview(row) }}
            </span>
          </template>
        </el-table-column>
      </el-table>

      <section v-if="focusedPacketActivityRow" class="event-inspector payload-inspector">
        <div class="event-inspector-main">
          <div class="event-inspector-head">
            <el-tag :type="packetActivityTagType(focusedPacketActivityRow)" effect="plain" size="small">
              {{ focusedPacketActivityRow.signalLabel }}
            </el-tag>
            <strong>{{ focusedPacketActivityRow.protocol.toUpperCase() }} {{ focusedPacketActivityRow.remoteLabel }}</strong>
            <span>{{ packetActivityDeltaLabel(focusedPacketActivityRow) }}</span>
            <span>{{ packetActivityDirectionLabel(focusedPacketActivityRow) }}</span>
          </div>
          <div class="event-reason">{{ focusedPacketActivityRow.signalReason }}</div>
        </div>

        <div class="payload-section-label">
          <strong>Payload 解析</strong>
          <span>优先看 JSON 片段；原始拼接用于排查。</span>
        </div>
        <div class="payload-analysis-panel" v-loading="packetActivityStreamLoading">
          <section class="payload-json-column payload-json-column--primary">
            <h4>JSON 片段</h4>
            <div v-if="!payloadStreamJsonFragments.length" class="json-empty">暂无 JSON 片段</div>
            <div
              v-for="fragment in payloadStreamJsonFragments"
              :key="fragment.label"
              class="payload-json-block"
            >
              <div class="payload-json-title">{{ fragment.label }}</div>
              <pre>{{ fragment.text }}</pre>
            </div>
          </section>
          <el-collapse v-model="payloadRawPanels" class="payload-raw-collapse">
            <el-collapse-item name="raw">
              <template #title>
                <span class="collapse-title">原始拼接</span>
                <span class="collapse-summary">{{ payloadRawSummary }}</span>
              </template>
              <div class="payload-stream-raw-column">
                <section
                  v-for="side in payloadStreamSides"
                  :key="side.name"
                  class="json-block payload-stream-block"
                >
                  <h4>
                    拼接{{ side.label }}
                    <el-tag v-if="side.data?.preview.length" :type="payloadTagType(side.data.preview.guess)" effect="plain" size="small">
                      {{ side.data.preview.guess }}
                    </el-tag>
                    <span class="payload-meta">{{ payloadStreamMeta(side.data) }}</span>
                  </h4>
                  <pre class="payload-stream-pre">{{ payloadStreamText(side.data) || '无 payload' }}</pre>
                  <template v-if="side.data?.preview.length && queryParamsFromPayloadText(payloadStreamText(side.data))">
                    <h4>Query</h4>
                    <pre>{{ queryParamsFromPayloadText(payloadStreamText(side.data)) }}</pre>
                  </template>
                </section>
              </div>
            </el-collapse-item>
          </el-collapse>
        </div>

        <div class="payload-section-label">
          <strong>最新单包</strong>
        </div>
        <div class="payload-detail-grid">
          <section
            v-for="side in [
              { name: 'up', label: '上行', data: focusedPacketActivityRow.flow?.payload_preview.up },
              { name: 'down', label: '下行', data: focusedPacketActivityRow.flow?.payload_preview.down },
            ]"
            :key="side.name"
            class="json-block"
            :class="{ 'payload-detail-block--json': payloadJsonFragments(payloadDisplayText(side.data)).length }"
          >
            <h4>
              {{ side.label }}
              <el-tag v-if="side.data?.length" :type="payloadTagType(side.data.guess)" effect="plain" size="small">
                {{ side.data.guess }}
              </el-tag>
              <span v-if="side.data?.length" class="payload-meta">{{ side.data.length }} B · 可读 {{ Math.round(side.data.printable_ratio * 100) }}%</span>
            </h4>
            <pre>{{ side.data?.length ? payloadDisplayText(side.data) : '无 payload' }}</pre>
            <div v-if="payloadJsonFragments(payloadDisplayText(side.data)).length" class="payload-json-fragments">
              <h4>JSON 片段</h4>
              <div
                v-for="fragment in payloadJsonFragments(payloadDisplayText(side.data), 2)"
                :key="fragment.label"
                class="payload-json-block"
              >
                <div class="payload-json-title">{{ fragment.label }}</div>
                <pre>{{ fragment.text }}</pre>
              </div>
            </div>
            <template v-if="side.data?.length && queryParamsFromPayloadText(payloadDisplayText(side.data))">
              <h4>Query</h4>
              <pre>{{ queryParamsFromPayloadText(payloadDisplayText(side.data)) }}</pre>
            </template>
            <template v-if="side.data?.length && !payloadHttpLine(side.data) && side.data.hex">
              <h4>Hex</h4>
              <pre>{{ side.data.hex }}</pre>
            </template>
          </section>
        </div>

        <section class="payload-history">
          <div class="payload-history-head">
            <h4>Payload 历史</h4>
            <span>{{ packetActivityHistoryTotal }} 条，当前会话内保留</span>
          </div>
          <el-table
            class="payload-history-table"
            :data="packetActivityHistoryItems"
            table-layout="fixed"
            :fit="true"
            :height="220"
            border
            v-loading="packetActivityHistoryLoading"
            empty-text="暂无 payload 历史"
          >
            <el-table-column label="时间" prop="captured_at" width="150" />
            <el-table-column label="方向" width="70">
              <template #default="{ row }">{{ payloadDirectionLabel(row.direction) }}</template>
            </el-table-column>
            <el-table-column label="判断" width="128">
              <template #default="{ row }">
                <el-tag :type="payloadTagType(row.payload_preview.guess)" effect="plain" size="small">
                  {{ row.payload_preview.guess }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="大小" width="118">
              <template #default="{ row }">{{ formatBytes(row.payload_bytes) }} / {{ formatBytes(row.packet_bytes) }}</template>
            </el-table-column>
            <el-table-column label="Payload" min-width="360">
              <template #default="{ row }">
                <span class="payload-preview-text">{{ payloadHistoryPreview(row) }}</span>
              </template>
            </el-table-column>
          </el-table>
          <div v-if="packetActivityHistoryTotal > 0" class="event-pagination">
            <el-pagination
              v-model:current-page="packetActivityHistoryPage"
              v-model:page-size="packetActivityHistoryPageSize"
              :total="packetActivityHistoryTotal"
              :page-sizes="[10, 20, 50, 100]"
              small
              background
              layout="total, sizes, prev, pager, next"
              @size-change="handlePacketActivityHistoryPageSizeChange"
              @current-change="handlePacketActivityHistoryPageChange"
            />
          </div>
        </section>
      </section>
    </section>

    <section v-else-if="activeMode === 'connections'" class="connection-observer">
      <div class="section-header">
        <div>
          <h3 class="section-title">TCP/UDP 实时</h3>
          <div class="proxy-status-line">
            <span>{{ connectionObserverSummary }}</span>
            <span v-if="snapshot">采样 {{ snapshot.captured_at }}</span>
            <span v-if="operationMarkedAt">标记 {{ formatClockTime(operationMarkedAt) }}</span>
          </div>
        </div>
        <div class="event-tools">
          <el-button
            class="observe-toggle-button"
            size="small"
            :type="connectionObserveRunning ? 'success' : 'info'"
            :plain="!connectionObserveRunning"
            @click="toggleConnectionObserve"
          >
            {{ connectionObserveLabel }}
          </el-button>
          <el-button size="small" @click="markOperation">标记操作</el-button>
          <el-button v-if="operationBaselineKeys" size="small" @click="clearOperationMark">清标记</el-button>
          <el-tooltip content="这里只看 MuMu 进程的远端 TCP/UDP 连接，并按公网、端口、Fake IP、进程归属等线索排序；不展示包内容。" placement="top">
            <el-icon class="help-icon"><QuestionFilled /></el-icon>
          </el-tooltip>
        </div>
      </div>

      <div v-if="!connectionCandidateRows.length" class="empty-guide">
        <strong>暂无疑似连接</strong>
        <span>保持观察中，然后在游戏里执行一次目标操作。</span>
      </div>

      <el-table
        v-else
        class="connection-candidate-table"
        :data="connectionCandidateRows"
        table-layout="fixed"
        :fit="true"
        border
        empty-text="暂无疑似连接"
      >
        <el-table-column label="判断" width="160">
          <template #default="{ row }">
            <div class="signal-cell">
              <el-tag :type="connectionSignalTagType(row)" effect="plain" size="small">
                {{ row.signal_label || '-' }}
              </el-tag>
              <span class="signal-score">{{ row.signal_score }}</span>
              <el-tag v-if="isNewAfterOperationMark(row)" type="danger" effect="plain" size="small">新增</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="连接" min-width="210">
          <template #default="{ row }">
            <span class="connection-endpoint">
              <strong>{{ row.protocol.toUpperCase() }}</strong>
              {{ row.remote?.label ?? '-' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="138">
          <template #default="{ row }">
            <span class="compact-cell-text">{{ row.status || connectionScopeLabel(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="首次/次数" width="118">
          <template #default="{ row }">{{ connectionSeenLabel(row) }}</template>
        </el-table-column>
        <el-table-column label="线索" min-width="360" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="preview-text">{{ row.signal_reason || '-' }}</span>
          </template>
        </el-table-column>
      </el-table>
    </section>

    <template v-else>
      <section class="capture-verdict" :class="{ 'capture-verdict--active': captureSessionActive }">
        <div class="capture-main">
          <div class="capture-toolbar">
            <el-button
              class="capture-toggle-button"
              size="small"
              :type="captureButtonType"
              :plain="!captureSessionActive"
              :loading="sessionLoading"
              @click="toggleCaptureSession"
            >
              {{ captureButtonLabel }}
            </el-button>
          </div>
          <div v-if="androidProxyStatus?.last_error" class="status-error">{{ androidProxyStatus.last_error }}</div>
          <div class="proxy-addresses">
            <span class="summary-label">安卓代理</span>
            <el-tag effect="plain" size="small">{{ recommendedProxyAddress }}</el-tag>
            <span v-if="androidProxyStatus?.device_id" class="summary-label">设备 {{ androidProxyStatus.device_id }}</span>
            <el-tooltip :content="captureDiagnosticText" placement="top">
              <el-icon class="help-icon"><QuestionFilled /></el-icon>
            </el-tooltip>
          </div>
        </div>
      </section>

      <section class="proxy-panel">
        <div class="section-header">
          <div>
            <h3 class="section-title">HTTP 事件</h3>
            <div class="proxy-status-line">
              <el-tooltip content="这里只覆盖走安卓系统代理的 HTTP/HTTPS；当前观察结果主要是上报、备案、DNS/SDK 请求。" placement="top">
                <span>{{ eventHeaderSummary }}</span>
              </el-tooltip>
            </div>
          </div>
          <div class="event-tools">
            <el-checkbox v-model="includeLowValueEvents" size="small">含资源/TLS</el-checkbox>
            <el-tooltip content="默认只看可读 HTTP 和接口候选；勾选后把资源下载、TLS 隧道等低价值事件也放回同一列表。" placement="top">
              <el-icon class="help-icon"><QuestionFilled /></el-icon>
            </el-tooltip>
          </div>
        </div>

        <el-alert
          v-if="proxyStatus?.last_error"
          class="warning-line"
          type="error"
          :closable="false"
          :title="proxyStatus.last_error"
        />

        <div v-if="!proxyEvents.length" class="empty-guide">
          <strong>暂无事件</strong>
          <span>{{ proxyEmptyStateText }}</span>
        </div>

        <template v-else>
          <section v-if="focusedProxyEvent" class="event-inspector event-inspector--json">
          <div class="event-inspector-main">
            <div class="event-inspector-head">
              <el-tag :type="signalTagType(focusedProxyEvent)" effect="plain" size="small">
                {{ focusedProxyEvent.signal_label || eventTypeLabel(focusedProxyEvent) }}
              </el-tag>
              <strong>{{ focusedProxyEvent.method || '-' }}</strong>
              <span>{{ focusedProxyEvent.response_status || focusedProxyEvent.error || (focusedProxyEvent.active ? 'active' : '-') }}</span>
              <span>{{ formatBytes(focusedProxyEvent.bytes_up) }} ↑ / {{ formatBytes(focusedProxyEvent.bytes_down) }} ↓</span>
            </div>
            <div class="event-target">{{ focusedProxyEvent.url || focusedProxyEvent.target || '-' }}</div>
            <div class="event-reason">{{ focusedProxyEvent.signal_reason || '-' }}</div>
          </div>
          <div v-if="focusedProxyJsonBlocks.length" class="json-blocks">
            <section v-for="block in focusedProxyJsonBlocks" :key="block.label" class="json-block">
              <h4>{{ block.label }}</h4>
              <pre>{{ block.text }}</pre>
            </section>
          </div>
          <div v-else class="json-empty">
            无 JSON 明文
          </div>
          <el-collapse v-model="liveRawPanels" class="raw-http-collapse">
            <el-collapse-item title="原始 HTTP" name="raw">
              <div class="event-body-grid">
                <div>
                  <h4>请求</h4>
                  <pre>{{ focusedProxyEvent.request_headers || '-' }}</pre>
                  <pre>{{ eventBodyPreview(focusedProxyEvent.request_body_text) }}</pre>
                </div>
                <div>
                  <h4>响应</h4>
                  <pre>{{ focusedProxyEvent.response_headers || focusedProxyEvent.response_status || '-' }}</pre>
                  <pre>{{ eventBodyPreview(focusedProxyEvent.response_body_text) }}</pre>
                </div>
              </div>
            </el-collapse-item>
          </el-collapse>
        </section>

        <el-table
          class="proxy-event-table"
          :data="filteredProxyEvents"
          table-layout="fixed"
          :fit="true"
          border
          :empty-text="proxyEventEmptyText"
          :row-class-name="proxyEventRowClass"
          @row-click="selectProxyEvent"
        >
          <el-table-column label="时间" prop="started_at" width="168" class-name="proxy-col-time" />
          <el-table-column label="判断" width="150" class-name="proxy-col-judgment">
            <template #default="{ row }">
              <div class="signal-cell">
                <el-tag :type="signalTagType(row)" effect="plain" size="small">
                  {{ row.signal_label || eventTypeLabel(row) }}
                </el-tag>
                <span v-if="row.signal_score" class="signal-score">{{ row.signal_score }}</span>
                <span class="event-type-mini">{{ eventTypeLabel(row) }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="方法" prop="method" width="54" class-name="proxy-col-method" />
          <el-table-column label="状态" width="152" class-name="proxy-col-status">
            <template #default="{ row }">
              <span class="compact-cell-text">{{ row.error || row.response_status || (row.active ? 'active' : '-') }}</span>
            </template>
          </el-table-column>
          <el-table-column label="JSON 摘要" min-width="360" class-name="proxy-col-json" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="preview-text preview-text--json">{{ jsonPreviewForEvent(row) }}</span>
            </template>
          </el-table-column>
        </el-table>

        <div v-if="eventTotal > 0" class="event-pagination">
          <el-pagination
            v-model:current-page="eventPage"
            v-model:page-size="eventPageSize"
            :total="eventTotal"
            :page-sizes="[20, 50, 100, 200]"
            small
            background
            layout="total, sizes, prev, pager, next"
            @size-change="handleEventPageSizeChange"
            @current-change="handleEventPageChange"
          />
          </div>
        </template>
      </section>
    </template>

    <el-collapse v-if="activeMode === 'connections'" v-model="detailPanels" class="detail-collapse">
      <el-collapse-item name="details">
        <template #title>
          <span class="collapse-title">诊断数据：连接 / DNS / 进程</span>
          <span class="collapse-summary">连接 {{ snapshot?.summary?.connection_count ?? 0 }}，MuMu {{ snapshot?.summary?.mumu_connection_count ?? 0 }}，Fake IP {{ snapshot?.summary?.fake_ip_connection_count ?? 0 }}</span>
        </template>

        <section class="control-row">
          <label class="field-label" for="fanxiu-packet-capture-hosts">
            DNS 探针
            <el-tooltip content="用于向 Clash 本地 DNS 查询 fake-ip 映射；每行一个域名。" placement="top">
              <el-icon class="help-icon"><QuestionFilled /></el-icon>
            </el-tooltip>
          </label>
          <el-input
            id="fanxiu-packet-capture-hosts"
            v-model="dnsHostText"
            class="host-input"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 4 }"
            placeholder="cdn-frxxz.akbing.com"
          />
          <div class="baseline-actions">
            <el-button :icon="SetUp" :disabled="!snapshot" @click="setBaseline">设基线</el-button>
            <el-button :disabled="!baselineKeys" @click="clearBaseline">清基线</el-button>
          </div>
        </section>

        <section class="summary-strip">
          <div v-for="item in summaryItems" :key="item.label" class="summary-item">
            <span class="summary-label">{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
          </div>
          <div class="summary-item">
            <span class="summary-label">新增</span>
            <strong>{{ newConnectionCount }}</strong>
          </div>
          <div class="summary-meta">
            <span v-if="snapshot">采样 {{ snapshot.captured_at }}</span>
            <span v-if="snapshot">DNS {{ snapshot.dns_server }}</span>
          </div>
        </section>

        <el-alert
          v-for="warning in snapshot?.warnings ?? []"
          :key="warning"
          class="warning-line"
          type="warning"
          :closable="false"
          :title="warning"
        />

        <el-tabs v-model="activeFilter" class="connection-tabs">
          <el-tab-pane label="全部连接" name="all" />
          <el-tab-pane label="MuMu" name="mumu" />
          <el-tab-pane label="Fake IP" name="fake" />
          <el-tab-pane label="代理外连" name="proxy" />
          <el-tab-pane label="新增" name="new" />
        </el-tabs>

        <el-table
          class="connection-table"
          :data="filteredConnections"
          table-layout="auto"
          :fit="false"
          border
          empty-text="暂无连接"
        >
          <el-table-column label="进程" min-width="170">
            <template #default="{ row }">
              <div class="process-cell">
                <el-tag :type="connectionTagType(row)" effect="plain" size="small">{{ row.process_group }}</el-tag>
                <span class="process-name">{{ row.process_name }}</span>
                <span class="pid">#{{ row.pid }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="协议" prop="protocol" width="72" />
          <el-table-column label="状态" prop="status" width="112" />
          <el-table-column label="本地" min-width="180">
            <template #default="{ row }">{{ row.local?.label ?? '-' }}</template>
          </el-table-column>
          <el-table-column label="远端" min-width="190">
            <template #default="{ row }">
              <div class="remote-cell">
                <el-icon v-if="row.is_fake_ip" class="fake-icon"><Connection /></el-icon>
                <span>{{ row.remote?.label ?? '-' }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="映射域名" min-width="220">
            <template #default="{ row }">{{ mappedHostLabel(row) }}</template>
          </el-table-column>
          <el-table-column label="" width="76" align="center">
            <template #default="{ row }">
              <el-tag v-if="isNewConnection(row)" type="warning" effect="plain" size="small">新增</el-tag>
            </template>
          </el-table-column>
        </el-table>

        <section class="lower-grid">
          <div>
            <h3 class="section-title">DNS 映射</h3>
            <el-table :data="snapshot?.dns_mappings ?? []" table-layout="auto" :fit="false" border empty-text="暂无映射">
              <el-table-column label="域名" prop="host" min-width="220" />
              <el-table-column label="Fake IP" min-width="180">
                <template #default="{ row }">{{ row.ips.length ? row.ips.join(', ') : '-' }}</template>
              </el-table-column>
              <el-table-column label="错误" prop="error" min-width="160" />
            </el-table>
          </div>

          <div>
            <h3 class="section-title">相关进程</h3>
            <el-table :data="snapshot?.processes ?? []" table-layout="auto" :fit="false" border empty-text="暂无进程">
              <el-table-column label="进程" min-width="180">
                <template #default="{ row }">
                  <span>{{ row.name }}</span>
                  <span class="pid">#{{ row.pid }}</span>
                </template>
              </el-table-column>
              <el-table-column label="分组" prop="group" width="86" />
              <el-table-column label="路径" prop="exe" min-width="260" show-overflow-tooltip />
            </el-table>
          </div>
        </section>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<style scoped>
.fanxiu-packet-capture-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: calc(100vh - 74px);
  padding: 18px 22px 28px;
  box-sizing: border-box;
  color: #1f2937;
  overflow: hidden;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}

.page-title {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
}

.mode-switch {
  display: inline-flex;
  align-self: flex-start;
  overflow: hidden;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
}

.mode-switch button {
  border: 0;
  border-right: 1px solid #dcdfe6;
  background: #fff;
  padding: 6px 14px;
  color: #4b5563;
  cursor: pointer;
  font-size: 13px;
}

.mode-switch button:last-child {
  border-right: 0;
}

.mode-switch button.active {
  background: #ecf5ff;
  color: #1677ff;
  font-weight: 650;
}

.connection-observer,
.tcp-decoder-panel,
.packet-activity-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
  padding: 12px 0 16px;
  border-top: 1px solid #e5e7eb;
  border-bottom: 1px solid #e5e7eb;
}

.tcp-decoder-panel {
  flex: 1;
}

.observe-toggle-button {
  min-width: 76px;
}

.connection-candidate-table,
.packet-activity-table {
  width: 100%;
}

.connection-candidate-table :deep(.el-table__cell),
.packet-activity-table :deep(.el-table__cell) {
  padding: 4px 0;
}

.connection-candidate-table :deep(.cell),
.packet-activity-table :deep(.cell) {
  padding: 0 8px;
  line-height: 24px;
  white-space: nowrap;
}

.connection-endpoint {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}

.connection-endpoint strong {
  color: #111827;
  font-size: 12px;
}

.capture-verdict {
  display: flex;
  align-items: flex-start;
  justify-content: flex-start;
  gap: 18px;
  border-left: 4px solid #d1d5db;
  background: #f9fafb;
  padding: 12px 16px;
}

.capture-verdict--active {
  border-left-color: #22c55e;
  background: #f0fdf4;
}

.capture-main {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.capture-toolbar {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  color: #6b7280;
  font-size: 12px;
}

.capture-toggle-button {
  min-width: 96px;
}

.status-error {
  margin-top: 6px;
  color: #b42318;
  font-size: 12px;
  line-height: 1.4;
}

.capture-main .proxy-addresses {
  min-height: 24px;
}

.control-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.field-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 76px;
  padding-top: 8px;
  color: #374151;
  font-size: 14px;
}

.help-icon {
  color: #909399;
  cursor: help;
}

.host-input {
  width: min(560px, 100%);
}

.tcp-simple-actions {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}

.tcp-plain-results {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
  padding: 10px 0 0;
}

.tcp-plain-table :deep(.el-table__cell) {
  padding: 4px 0;
}

.tcp-plain-table :deep(.cell) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tcp-plain-table :deep(.el-table__row) {
  cursor: pointer;
}

.tcp-business-content {
  white-space: nowrap;
}

.tcp-business-param {
  display: inline-block;
  margin: 0 1px;
  padding: 0 3px;
  border-radius: 3px;
  background: #fff7ed;
  color: #b45309;
  font-weight: 600;
}

.tcp-plain-table :deep(.el-table__row.is-upstream-event > td.el-table__cell) {
  background: #f5f6f8;
}

.tcp-business-pagination {
  display: flex;
  justify-content: flex-end;
  padding-top: 10px;
}

.tcp-business-detail {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
  margin-top: 10px;
  width: 100%;
}

.tcp-business-detail pre {
  flex: 1;
  min-height: 0;
  height: auto;
  max-height: none;
}

.subsection-title {
  margin: 0 0 8px;
  color: #111827;
  font-size: 13px;
  font-weight: 650;
}

.summary-strip {
  display: flex;
  align-items: center;
  gap: 18px;
  min-height: 38px;
  padding: 0 0 10px;
  border-bottom: 1px solid #e5e7eb;
}

.summary-item {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
}

.summary-label,
.summary-meta,
.pid {
  color: #6b7280;
  font-size: 12px;
}

.summary-item strong {
  font-size: 18px;
  font-weight: 650;
}

.summary-meta {
  display: flex;
  gap: 12px;
  margin-left: auto;
}

.warning-line {
  max-width: 860px;
}

.baseline-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.proxy-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 0 16px;
  border-top: 1px solid #e5e7eb;
  border-bottom: 1px solid #e5e7eb;
}

.section-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
}

.event-tools,
.proxy-status-line,
.proxy-addresses {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.event-tools {
  min-height: 24px;
}

.packet-guide-collapse {
  border-top: 1px solid #eef2f7;
  border-bottom: 1px solid #eef2f7;
}

.packet-guide-collapse :deep(.el-collapse-item__header) {
  height: 32px;
  color: #374151;
  font-size: 13px;
  font-weight: 650;
}

.packet-guide-collapse :deep(.el-collapse-item__wrap) {
  border-bottom: 0;
}

.packet-guide-list {
  margin: 0;
  padding: 2px 0 8px 20px;
  color: #4b5563;
  font-size: 13px;
  line-height: 1.8;
}

.proxy-status-line {
  margin-top: 6px;
  color: #6b7280;
  font-size: 12px;
}

.connection-tabs {
  margin-bottom: -8px;
}

.connection-table {
  width: 100%;
}

.proxy-event-table {
  width: 100%;
}

.proxy-event-table :deep(.el-table__cell) {
  padding: 3px 0;
}

.proxy-event-table :deep(.cell) {
  padding: 0 8px;
  overflow: visible;
  line-height: 22px;
  text-overflow: clip;
  white-space: nowrap;
}

.proxy-event-table :deep(.proxy-col-json .cell) {
  overflow: hidden;
  text-overflow: ellipsis;
}

.proxy-event-table :deep(.el-table__row) {
  cursor: pointer;
}

.packet-activity-table :deep(.el-table__row) {
  cursor: pointer;
}

.proxy-event-table :deep(.el-table__row.is-selected-event > td.el-table__cell),
.tcp-plain-table :deep(.el-table__row.is-selected-event > td.el-table__cell),
.packet-activity-table :deep(.el-table__row.is-selected-event > td.el-table__cell) {
  background: #eff6ff;
}

.event-pagination {
  display: flex;
  justify-content: flex-end;
  padding-top: 2px;
}

.empty-guide {
  display: flex;
  align-items: center;
  gap: 10px;
  max-width: max-content;
  border: 1px solid #e5e7eb;
  background: #fff;
  padding: 10px 12px;
  color: #374151;
  font-size: 13px;
  line-height: 1.4;
}

.empty-guide strong {
  color: #111827;
  font-size: 14px;
}

.event-inspector {
  display: flex;
  flex-direction: column;
  gap: 10px;
  border: 1px solid #e5e7eb;
  background: #fff;
  padding: 12px;
}

.payload-inspector {
  height: clamp(560px, 70vh, 820px);
  overflow-x: hidden;
  overflow-y: auto;
}

.event-inspector-main {
  min-width: 0;
}

.event-inspector-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  color: #6b7280;
  font-size: 12px;
}

.event-inspector-head strong {
  color: #111827;
  font-size: 14px;
}

.event-target {
  margin-top: 8px;
  overflow-wrap: anywhere;
  color: #6b7280;
  font-size: 12px;
  line-height: 1.5;
}

.event-reason {
  margin-top: 8px;
  color: #4b5563;
  font-size: 12px;
  line-height: 1.5;
}

.json-blocks {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.payload-detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(260px, 1fr));
  gap: 12px;
  min-width: 0;
}

.payload-analysis-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}

.payload-raw-collapse {
  min-width: 0;
  overflow: hidden;
  border-top: 1px solid #e5e7eb;
  border-bottom: 1px solid #e5e7eb;
}

.payload-raw-collapse :deep(.el-collapse-item__header) {
  height: 34px;
  padding: 0 8px;
  color: #374151;
  font-size: 13px;
  font-weight: 650;
}

.payload-raw-collapse :deep(.el-collapse-item__wrap) {
  border-bottom: 0;
}

.payload-raw-collapse .payload-stream-raw-column {
  display: grid;
  grid-template-columns: repeat(2, minmax(260px, 1fr));
  gap: 10px;
  padding: 8px 0 2px;
}

.payload-stream-raw-column,
.payload-json-column {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}

.payload-json-column h4 {
  margin: 0;
  color: #111827;
  font-size: 13px;
  font-weight: 650;
}

.payload-json-column--primary {
  border: 1px solid #e5e7eb;
  background: #fff;
  padding: 10px;
}

.payload-detail-block--json {
  grid-column: 1 / -1;
}

.payload-section-label {
  display: flex;
  align-items: baseline;
  gap: 10px;
  color: #6b7280;
  font-size: 12px;
}

.payload-section-label strong {
  color: #111827;
  font-size: 13px;
  font-weight: 650;
}

.payload-meta {
  margin-left: 8px;
  color: #6b7280;
  font-size: 12px;
  font-weight: 400;
}

.payload-history {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.payload-history-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  color: #6b7280;
  font-size: 12px;
}

.payload-history-head h4 {
  margin: 0;
  color: #111827;
  font-size: 13px;
  font-weight: 650;
}

.payload-history-table :deep(.el-table__cell) {
  padding: 3px 0;
}

.payload-history-table :deep(.cell) {
  padding: 0 8px;
  line-height: 22px;
  white-space: nowrap;
}

.json-block {
  min-width: 0;
}

.json-block h4 {
  margin: 0 0 6px;
  color: #111827;
  font-size: 13px;
  font-weight: 650;
}

.json-block pre {
  max-height: 420px;
  margin: 0;
  overflow: auto;
  border: 1px solid #dbeafe;
  background: #fbfdff;
  padding: 10px 12px;
  color: #111827;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.payload-stream-pre {
  height: 132px;
  max-height: 132px;
}

.payload-json-fragments {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
}

.payload-json-block {
  min-width: 0;
}

.payload-json-title {
  margin-bottom: 4px;
  color: #6b7280;
  font-size: 12px;
  font-weight: 650;
}

.payload-json-block pre {
  height: 340px;
  max-height: 340px;
  border-color: #bbf7d0;
  background: #f7fff9;
}

.payload-inspector .payload-detail-grid > .json-block > pre {
  height: 76px;
  max-height: 76px;
}

.payload-inspector .payload-stream-raw-column > .json-block > pre:not(.payload-stream-pre) {
  height: 76px;
  max-height: 76px;
}

.json-empty {
  border: 1px dashed #d1d5db;
  background: #f9fafb;
  padding: 12px;
  color: #6b7280;
  font-size: 13px;
  line-height: 1.6;
}

.raw-http-collapse {
  border-top: 1px solid #edf2f7;
  border-bottom: 0;
}

.raw-http-collapse :deep(.el-collapse-item__header) {
  height: 32px;
  color: #6b7280;
  font-size: 12px;
}

.raw-http-collapse :deep(.el-collapse-item__wrap) {
  border-bottom: 0;
}

.event-body-grid {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr);
  gap: 12px;
  min-width: 0;
}

.event-body-grid h4 {
  margin: 0 0 6px;
  color: #374151;
  font-size: 13px;
  font-weight: 650;
}

.event-body-grid pre {
  max-height: 180px;
  margin: 0 0 8px;
  overflow: auto;
  border: 1px solid #e5e7eb;
  background: #f9fafb;
  padding: 8px;
  color: #111827;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}

.process-cell,
.remote-cell {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  white-space: nowrap;
}

.process-name {
  font-weight: 500;
}

.fake-icon {
  color: #2563eb;
}

.preview-text {
  display: inline-block;
  max-width: 520px;
  overflow: hidden;
  color: #374151;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}

.preview-text--json {
  width: 100%;
  max-width: none;
  color: #111827;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}

.payload-preview-text {
  display: inline-block;
  width: 100%;
  overflow: hidden;
  color: #111827;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}

.compact-cell-text {
  display: inline-block;
  width: 100%;
  overflow: visible;
  text-overflow: clip;
  white-space: nowrap;
  vertical-align: bottom;
}

.signal-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: nowrap;
  max-width: 100%;
  min-width: 0;
  overflow: hidden;
}

.signal-score,
.event-type-mini {
  color: #6b7280;
  font-size: 11px;
  line-height: 1;
  white-space: nowrap;
}

.signal-score {
  font-variant-numeric: tabular-nums;
}

.lower-grid {
  display: grid;
  grid-template-columns: minmax(320px, 0.9fr) minmax(360px, 1.1fr);
  gap: 18px;
  align-items: start;
}

.detail-collapse {
  border-top: 1px solid #e5e7eb;
  border-bottom: 1px solid #e5e7eb;
}

.collapse-title {
  font-weight: 650;
}

.collapse-summary {
  margin-left: 12px;
  overflow: hidden;
  color: #6b7280;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.section-title {
  margin: 6px 0 10px;
  font-size: 15px;
  font-weight: 650;
}

@media (max-width: 960px) {
  .page-header,
  .capture-verdict,
  .control-row {
    flex-direction: column;
  }

  .section-header,
  .summary-strip,
  .summary-meta {
    flex-wrap: wrap;
  }

  .section-header {
    flex-direction: column;
  }

  .summary-meta {
    margin-left: 0;
  }

  .event-body-grid,
  .payload-raw-collapse .payload-stream-raw-column,
  .payload-detail-grid,
  .lower-grid {
    grid-template-columns: 1fr;
  }
}
</style>
