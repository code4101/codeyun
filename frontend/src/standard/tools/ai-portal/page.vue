<template>
  <div class="ai-portal-page">
    <header class="page-header">
      <div>
        <div class="eyebrow">AI工具 / 门户</div>
        <h1>AI 门户</h1>
        <p class="page-desc">
          把常用的对话站点收进同一个外壳，能内嵌的直接内嵌，被站点拒绝的用新窗口打开。
        </p>
      </div>
      <div class="header-actions">
        <el-button :icon="Plus" @click="openAddSite">
          添加站点
        </el-button>
      </div>
    </header>

    <div class="portal-body">
      <aside class="site-rail">
        <el-input
          v-model="filterKeyword"
          size="small"
          clearable
          placeholder="搜索站点"
          :prefix-icon="Search"
        />

        <div class="site-scroll">
          <div v-for="group in groupedSites" :key="group.name" class="site-group">
            <div class="site-group-title">{{ group.name }}</div>
            <button
              v-for="site in group.sites"
              :key="site.id"
              type="button"
              class="site-item"
              :class="{ 'is-active': site.id === activeSiteId }"
              @click="selectSite(site)"
            >
              <span class="site-badge">{{ siteInitials(site.name) }}</span>
              <span class="site-meta">
                <span class="site-name">{{ site.name }}</span>
                <span class="site-host">{{ siteHostname(site.url) }}</span>
              </span>
              <span
                class="site-dot"
                :class="`is-${site.embed}`"
                :title="embedHint(site)"
              />
              <span
                v-if="site.custom"
                class="site-remove"
                title="移除该站点"
                @click.stop="removeCustomSite(site)"
              >×</span>
            </button>
          </div>

          <el-empty
            v-if="!groupedSites.length"
            description="没有匹配的站点"
            :image-size="60"
          />
        </div>
      </aside>

      <section class="viewer panel-card">
        <div class="viewer-toolbar">
          <div class="viewer-title">
            <span class="viewer-name">{{ activeSite?.name ?? '未选择站点' }}</span>
            <el-tag
              v-if="activeSite"
              size="small"
              effect="plain"
              :type="EMBED_TAG_TYPE[activeSite.embed]"
            >
              {{ embedLabel(activeSite) }}
            </el-tag>
          </div>

          <div class="viewer-actions">
            <div class="mode-switch">
              <button
                type="button"
                class="mode-btn"
                :class="{ 'is-active': viewMode === 'embed' }"
                @click="viewMode = 'embed'"
              >嵌入</button>
              <button
                type="button"
                class="mode-btn"
                :class="{ 'is-active': viewMode === 'external' }"
                @click="viewMode = 'external'"
              >新窗口</button>
            </div>
            <el-button
              size="small"
              :icon="Refresh"
              :disabled="!activeSite || viewMode !== 'embed'"
              @click="reloadFrame"
            >刷新</el-button>
            <el-button
              size="small"
              :icon="Link"
              :disabled="!activeSite"
              @click="copyUrl"
            >复制链接</el-button>
            <el-button
              size="small"
              type="primary"
              :icon="TopRight"
              :disabled="!activeSite"
              @click="openExternal(activeSite?.url)"
            >新窗口打开</el-button>
          </div>
        </div>

        <el-alert
          v-if="activeSite && activeSite.embed === 'blocked' && viewMode === 'embed' && forceEmbed"
          class="viewer-alert"
          type="warning"
          :closable="false"
          show-icon
          title="该站点在响应头里声明禁止被嵌套，下面大概率是空白"
        >
          {{ activeSite.note ?? '站点通过 X-Frame-Options / CSP frame-ancestors 拒绝被其他网站嵌入。' }}
          浏览器会强制拒绝，前端无法绕过，建议切到“新窗口”。
        </el-alert>

        <el-alert
          v-else-if="activeSite && activeSite.embed === 'unknown' && viewMode === 'embed'"
          class="viewer-alert"
          type="info"
          :closable="false"
          show-icon
          title="该站点是否允许嵌套未经验证"
        >
          如果长时间空白或登录失败，切换到“新窗口”即可。嵌入内的登录依赖浏览器允许第三方 Cookie。
        </el-alert>

        <el-alert
          v-if="frameStalled && viewMode === 'embed' && activeSite?.embed !== 'blocked'"
          class="viewer-alert"
          type="warning"
          :closable="false"
          show-icon
          title="页面加载较慢或已被站点拦截"
        >
          可以点“刷新”重试，或直接用“新窗口打开”。
        </el-alert>

        <div class="viewer-stage">
          <el-empty
            v-if="!activeSite"
            description="从左侧选择一个站点开始"
            :image-size="88"
          />

          <iframe
            v-else-if="viewMode === 'embed' && !showBlockedFallback"
            :key="frameKey"
            class="portal-frame"
            :src="activeSite.url"
            :title="activeSite.name"
            allow="clipboard-read; clipboard-write; fullscreen; microphone; camera; geolocation"
            @load="onFrameLoad"
          />

          <div v-else-if="showBlockedFallback" class="external-panel">
            <el-icon class="external-icon is-blocked">
              <Warning />
            </el-icon>
            <div class="external-name">{{ activeSite.name }}</div>
            <div class="external-host">该站点声明禁止被其他网站嵌套</div>
            <p class="external-note">
              {{ activeSite.note ?? 'X-Frame-Options / CSP frame-ancestors 限制' }}，浏览器会强制拒绝加载，所以这里不放 iframe。
              新窗口打开不受此限制，登录也最稳定。
            </p>
            <div class="external-actions">
              <el-button type="primary" :icon="TopRight" @click="openExternal(activeSite.url)">
                在新窗口打开
              </el-button>
              <el-button @click="tryEmbedBlockedSite">
                仍要尝试嵌入
              </el-button>
            </div>
          </div>

          <div v-else class="external-panel">
            <el-icon class="external-icon">
              <TopRight />
            </el-icon>
            <div class="external-name">{{ activeSite.name }}</div>
            <div class="external-host">{{ activeSite.url }}</div>
            <el-button type="primary" :icon="TopRight" @click="openExternal(activeSite.url)">
              在新窗口打开
            </el-button>
            <p class="external-note">
              新窗口里登录最稳定；登录后再回到本页刷新即可复用浏览器会话。
            </p>
          </div>
        </div>
      </section>
    </div>

    <el-dialog v-model="addDialogVisible" title="添加站点" width="460px">
      <el-form label-width="64px" @submit.prevent>
        <el-form-item label="名称">
          <el-input v-model="draftName" placeholder="例如 我的中转站" @keyup.enter="confirmAddSite" />
        </el-form-item>
        <el-form-item label="网址">
          <el-input v-model="draftUrl" placeholder="https://example.com" @keyup.enter="confirmAddSite" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="addDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmAddSite">添加</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Link, Plus, Refresh, Search, TopRight, Warning } from '@element-plus/icons-vue'

import {
  AI_PORTAL_PRESET_SITES,
  createCustomSite,
  loadActiveSiteId,
  loadCustomSites,
  loadViewMode,
  saveActiveSiteId,
  saveCustomSites,
  saveViewMode,
  siteHostname,
  siteInitials,
  type AiPortalEmbedStatus,
  type AiPortalSite,
  type AiPortalViewMode,
} from './sites'

const EMBED_LABEL: Record<AiPortalEmbedStatus, string> = {
  ok: '可嵌入',
  unknown: '未验证',
  blocked: '禁止嵌套',
}

const EMBED_TAG_TYPE: Record<AiPortalEmbedStatus, 'success' | 'info' | 'danger'> = {
  ok: 'success',
  unknown: 'info',
  blocked: 'danger',
}

const customSites = ref<AiPortalSite[]>(loadCustomSites())
const activeSiteId = ref<string | null>(loadActiveSiteId())
const viewMode = ref<AiPortalViewMode>(loadViewMode())
const filterKeyword = ref('')

const allSites = computed<AiPortalSite[]>(() => [
  ...AI_PORTAL_PRESET_SITES,
  ...customSites.value,
])

const activeSite = computed<AiPortalSite | null>(
  () => allSites.value.find((site) => site.id === activeSiteId.value) ?? null,
)

const showBlockedFallback = computed(() => (
  viewMode.value === 'embed'
  && activeSite.value?.embed === 'blocked'
  && !forceEmbed.value
))

const groupedSites = computed(() => {
  const keyword = filterKeyword.value.trim().toLowerCase()
  const groups: { name: string; sites: AiPortalSite[] }[] = []
  const groupIndex = new Map<string, number>()

  for (const site of allSites.value) {
    if (keyword && !`${site.name} ${site.url}`.toLowerCase().includes(keyword)) {
      continue
    }
    let index = groupIndex.get(site.group)
    if (index === undefined) {
      index = groups.length
      groupIndex.set(site.group, index)
      groups.push({ name: site.group, sites: [] })
    }
    groups[index].sites.push(site)
  }

  return groups
})

if (!activeSiteId.value || !allSites.value.some((site) => site.id === activeSiteId.value)) {
  activeSiteId.value = allSites.value.find((site) => site.embed === 'ok')?.id
    ?? allSites.value[0]?.id
    ?? null
}

const frameKey = ref(0)
const frameLoaded = ref(false)
const frameStalled = ref(false)
const forceEmbed = ref(false)
let stallTimer: ReturnType<typeof setTimeout> | null = null

function clearStallTimer() {
  if (stallTimer) {
    clearTimeout(stallTimer)
    stallTimer = null
  }
}

function resetFrame() {
  frameLoaded.value = false
  frameStalled.value = false
  clearStallTimer()
  stallTimer = setTimeout(() => {
    frameStalled.value = !frameLoaded.value
  }, 4500)
}

function reloadFrame() {
  frameKey.value += 1
  resetFrame()
}

function onFrameLoad() {
  frameLoaded.value = true
  frameStalled.value = false
  clearStallTimer()
}

function selectSite(site: AiPortalSite) {
  activeSiteId.value = site.id
}

function embedLabel(site: AiPortalSite) {
  return EMBED_LABEL[site.embed]
}

function embedHint(site: AiPortalSite) {
  return site.note ? `${EMBED_LABEL[site.embed]}：${site.note}` : EMBED_LABEL[site.embed]
}

function openExternal(url: string | undefined) {
  if (!url) {
    return
  }
  window.open(url, '_blank', 'noopener,noreferrer')
}

function tryEmbedBlockedSite() {
  forceEmbed.value = true
  frameKey.value += 1
  resetFrame()
}

async function copyUrl() {
  const site = activeSite.value
  if (!site) {
    return
  }
  try {
    await navigator.clipboard.writeText(site.url)
    ElMessage.success('已复制链接')
  } catch {
    ElMessage.warning('复制失败，请手动复制')
  }
}

const addDialogVisible = ref(false)
const draftName = ref('')
const draftUrl = ref('')

function openAddSite() {
  draftName.value = ''
  draftUrl.value = ''
  addDialogVisible.value = true
}

function confirmAddSite() {
  let site: AiPortalSite
  try {
    site = createCustomSite(draftName.value, draftUrl.value)
  } catch {
    ElMessage.error('请输入有效的网址')
    return
  }

  if (allSites.value.some((item) => item.url === site.url)) {
    ElMessage.warning('该站点已在列表中')
    return
  }

  customSites.value = [...customSites.value, site]
  saveCustomSites(customSites.value)
  activeSiteId.value = site.id
  viewMode.value = 'embed'
  addDialogVisible.value = false
  ElMessage.success('已添加站点')
}

async function removeCustomSite(site: AiPortalSite) {
  try {
    await ElMessageBox.confirm(`确定移除「${site.name}」吗？`, '移除站点', {
      type: 'warning',
      confirmButtonText: '移除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }

  customSites.value = customSites.value.filter((item) => item.id !== site.id)
  saveCustomSites(customSites.value)
  if (activeSiteId.value === site.id) {
    activeSiteId.value = allSites.value[0]?.id ?? null
  }
}

watch(activeSiteId, (value) => {
  if (value) {
    saveActiveSiteId(value)
  }
  forceEmbed.value = false
})

watch(viewMode, (value) => {
  saveViewMode(value)
  forceEmbed.value = false
  if (value === 'embed') {
    frameKey.value += 1
    resetFrame()
  } else {
    clearStallTimer()
    frameStalled.value = false
  }
})

resetFrame()

onBeforeUnmount(clearStallTimer)
</script>

<style scoped>
.ai-portal-page {
  height: 100%;
  min-height: 0;
  padding: 20px;
  box-sizing: border-box;
  background: linear-gradient(180deg, #f7fafc 0%, #eef3f6 100%);
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow: hidden;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.eyebrow {
  margin-bottom: 4px;
  color: #64748b;
  font-size: 12px;
}

.page-header h1 {
  margin: 0;
  color: #0f172a;
  font-size: 22px;
  line-height: 1.2;
}

.page-desc {
  margin: 6px 0 0;
  color: #64748b;
  font-size: 13px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.portal-body {
  display: grid;
  grid-template-columns: minmax(220px, 260px) minmax(0, 1fr);
  gap: 14px;
  flex: 1;
  min-height: 0;
}

.panel-card {
  border-radius: 8px;
  border: 1px solid rgba(203, 213, 225, 0.82);
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.06);
}

.site-rail {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
  padding: 12px;
  border-radius: 8px;
  border: 1px solid rgba(203, 213, 225, 0.82);
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.06);
}

.site-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-right: 2px;
}

.site-group + .site-group {
  margin-top: 12px;
}

.site-group-title {
  margin: 4px 4px 6px;
  color: #94a3b8;
  font-size: 12px;
  letter-spacing: 0.02em;
}

.site-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 8px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.site-item:hover {
  background: #f1f5f9;
}

.site-item.is-active {
  border-color: #bfdbfe;
  background: #eff6ff;
}

.site-badge {
  flex: 0 0 auto;
  width: 28px;
  height: 28px;
  border-radius: 7px;
  background: #e2e8f0;
  color: #475569;
  font-size: 12px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.site-item.is-active .site-badge {
  background: #dbeafe;
  color: #1d4ed8;
}

.site-meta {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
  flex: 1;
}

.site-name {
  color: #0f172a;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.site-host {
  color: #94a3b8;
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.site-dot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #cbd5e1;
}

.site-dot.is-ok {
  background: #22c55e;
}

.site-dot.is-unknown {
  background: #f59e0b;
}

.site-dot.is-blocked {
  background: #ef4444;
}

.site-remove {
  flex: 0 0 auto;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  color: #94a3b8;
  font-size: 15px;
  line-height: 16px;
  text-align: center;
}

.site-remove:hover {
  background: #fee2e2;
  color: #dc2626;
}

.viewer {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.viewer-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 14px;
  border-bottom: 1px solid #e2e8f0;
}

.viewer-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.viewer-name {
  color: #0f172a;
  font-size: 15px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.viewer-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.mode-switch {
  display: inline-flex;
  padding: 2px;
  border-radius: 8px;
  background: #f1f5f9;
}

.mode-btn {
  padding: 4px 12px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #64748b;
  font-size: 13px;
  cursor: pointer;
}

.mode-btn.is-active {
  background: #fff;
  color: #1d4ed8;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.12);
}

.viewer-alert {
  margin: 12px 14px 0;
}

.viewer-stage {
  flex: 1;
  min-height: 0;
  display: flex;
  margin: 12px 14px 14px;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  overflow: hidden;
}

.viewer-stage > :deep(.el-empty) {
  margin: auto;
}

.portal-frame {
  width: 100%;
  height: 100%;
  border: 0;
  background: #fff;
}

.external-panel {
  margin: auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 28px;
  text-align: center;
}

.external-icon {
  font-size: 34px;
  color: #94a3b8;
}

.external-icon.is-blocked {
  color: #f59e0b;
}

.external-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 4px;
}

.external-name {
  color: #0f172a;
  font-size: 18px;
  font-weight: 600;
}

.external-host {
  color: #94a3b8;
  font-size: 12px;
  word-break: break-all;
}

.external-note {
  margin: 4px 0 0;
  max-width: 360px;
  color: #64748b;
  font-size: 12px;
  line-height: 1.6;
}

@media (max-width: 900px) {
  .ai-portal-page {
    height: auto;
    overflow: visible;
    padding: 16px 14px 24px;
  }

  .page-header {
    flex-direction: column;
    align-items: stretch;
  }

  .portal-body {
    grid-template-columns: minmax(0, 1fr);
  }

  .site-scroll {
    max-height: 220px;
  }

  .viewer {
    min-height: 70vh;
  }
}
</style>
