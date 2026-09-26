<!--
  数字足迹自查页

  只读展示 auditData.ts 中的四层数据：时间线 / 标识符 / 采集源 / 发现。
  页面本身不含逻辑分支，后续接入真实采集时只改数据源，不改渲染。
-->
<script setup lang="ts">
import { computed } from 'vue'

import {
  collectionAdapters,
  derivedBirthYear,
  findings,
  seedIdentifiers,
  timelineNodes,
  type AdapterStatus,
  type Confidence,
  type Sensitivity,
} from './data/auditData'

const STATUS_TEXT: Record<AdapterStatus, string> = {
  todo: '待运行',
  running: '运行中',
  done: '已完成',
  skipped: '已跳过',
}

const CONFIDENCE_TEXT: Record<Confidence, string> = {
  known: '已确认',
  derived: '推证',
  unknown: '待确认',
}

const SENSITIVITY_TEXT: Record<Sensitivity, string> = {
  low: '低',
  medium: '中',
  high: '高',
}

const doneAdapterCount = computed(
  () => collectionAdapters.filter((item) => item.status === 'done').length,
)

const derivedNodeCount = computed(
  () => timelineNodes.filter((item) => item.confidence !== 'known').length,
)

const highRiskSeedCount = computed(
  () => seedIdentifiers.filter((item) => item.sensitivity !== 'low').length,
)

function formatRange(start: string, end: string) {
  if (!start && !end) return '时间未知'
  const from = start || '?'
  const to = end || '?'
  return `${from} ~ ${to}`
}
</script>

<template>
  <div class="footprint-page">
    <header class="page-head">
      <h1>数字足迹自查</h1>
      <p class="page-desc">
        把网上公开可搜到的本人数据按来源分层汇总。上层是标识符与履历，下层是各采集源的命中结果；
        逐条补全即可得到一份可定期复跑的隐私暴露报告。
      </p>

      <div class="summary-row">
        <div class="summary-card">
          <span class="summary-value">{{ seedIdentifiers.length }}</span>
          <span class="summary-label">标识符入口</span>
        </div>
        <div class="summary-card">
          <span class="summary-value">{{ highRiskSeedCount }}</span>
          <span class="summary-label">中高敏感标识符</span>
        </div>
        <div class="summary-card">
          <span class="summary-value">{{ derivedNodeCount }}</span>
          <span class="summary-label">需标注推证的时间线节点</span>
        </div>
        <div class="summary-card">
          <span class="summary-value">{{ doneAdapterCount }} / {{ collectionAdapters.length }}</span>
          <span class="summary-label">已完成的采集源</span>
        </div>
      </div>
    </header>

    <section class="block">
      <h2>时间线倒推</h2>
      <p class="block-desc">
        已知履历是锚点，学制是推证规则。小学到高中三点全部由「本科入学 2011-09」倒推，
        因此只要拿到学历信息，就能直接定位到中小学的年份区间与大致年龄段。
      </p>

      <div class="timeline">
        <div
          v-for="node in timelineNodes"
          :key="node.id"
          class="timeline-node"
          :class="`confidence-${node.confidence}`"
        >
          <div class="timeline-range">{{ formatRange(node.start, node.end) }}</div>
          <div class="timeline-body">
            <div class="timeline-title">
              <span class="stage-tag">{{ node.stage }}</span>
              <span class="org-name">{{ node.org }}</span>
              <span class="confidence-tag" :class="`confidence-${node.confidence}`">
                {{ CONFIDENCE_TEXT[node.confidence] }}
              </span>
            </div>
            <p class="timeline-basis">{{ node.basis }}</p>
          </div>
        </div>
      </div>

      <p class="note-line">
        据此可进一步收缩出生年：<strong>{{ derivedBirthYear.from }}–{{ derivedBirthYear.to }}</strong>
        （{{ derivedBirthYear.basis }}）。这是「学校 + 入学年份」类信息公开后最常见的连带泄露。
      </p>
    </section>

    <section class="block">
      <h2>标识符入口</h2>
      <p class="block-desc">这些是检索的种子。strong 表示能唯一定位到本人，weak 表示只用于交叉验证。</p>

      <table class="data-table">
        <thead>
          <tr>
            <th>维度</th>
            <th>值</th>
            <th>敏感度</th>
            <th>检索强度</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="seed in seedIdentifiers" :key="seed.id">
            <td>{{ seed.kind }}</td>
            <td class="value-cell">{{ seed.value }}</td>
            <td>
              <span class="sensitivity-tag" :class="`sensitivity-${seed.sensitivity}`">
                {{ SENSITIVITY_TEXT[seed.sensitivity] }}
              </span>
            </td>
            <td>{{ seed.uniqueness === 'strong' ? '强标识' : '弱标识' }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="block">
      <h2>采集源</h2>
      <p class="block-desc">
        一个数据源一个适配器，可单独启停、单独限速。建议按「用户名枚举 → 搜索引擎 → 泄露自查」的顺序跑，
        先拿到账号清单，再做交叉验证。
      </p>

      <table class="data-table">
        <thead>
          <tr>
            <th>采集源</th>
            <th>覆盖维度</th>
            <th>推荐工具</th>
            <th>状态</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="adapter in collectionAdapters" :key="adapter.id">
            <td>{{ adapter.name }}</td>
            <td>{{ adapter.dimension }}</td>
            <td class="value-cell">{{ adapter.tool }}</td>
            <td>
              <span class="status-tag" :class="`status-${adapter.status}`">
                {{ STATUS_TEXT[adapter.status] }}
              </span>
            </td>
            <td class="note-cell">{{ adapter.note }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="block">
      <h2>发现</h2>
      <p v-if="findings.length === 0" class="empty-state">
        暂无采集结果。跑完任一个采集源后，把命中项写入 auditData.ts 的 findings 即可在此汇总。
      </p>
      <table v-else class="data-table">
        <thead>
          <tr>
            <th>字段</th>
            <th>值</th>
            <th>来源</th>
            <th>敏感度</th>
            <th>采集时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="finding in findings" :key="finding.id">
            <td>{{ finding.field }}</td>
            <td class="value-cell">{{ finding.value }}</td>
            <td>
              <a v-if="finding.sourceUrl" :href="finding.sourceUrl" target="_blank" rel="noreferrer">
                {{ finding.sourceUrl }}
              </a>
              <span v-else>-</span>
            </td>
            <td>
              <span class="sensitivity-tag" :class="`sensitivity-${finding.sensitivity}`">
                {{ SENSITIVITY_TEXT[finding.sensitivity] }}
              </span>
            </td>
            <td>{{ finding.collectedAt || '-' }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="block">
      <h2>使用边界</h2>
      <ul class="boundary-list">
        <li>只用于本人自查、本人授权，或对自有资产的暴露面评估。</li>
        <li>不采集住址、行踪、亲属关系等定位类信息，也不接入来源不明的泄露数据。</li>
        <li>本页数据用权限节点隔离（默认不允许匿名访问），避免自查页本身变成泄露点。</li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.footprint-page {
  min-height: 100%;
  padding: 32px 40px 56px;
  background: #f6f8fb;
  box-sizing: border-box;
  color: #1f2937;
}

.page-head {
  margin-bottom: 28px;
}

h1 {
  margin: 0 0 12px;
  font-size: 28px;
  color: #111827;
}

.page-desc,
.block-desc {
  margin: 0;
  max-width: 900px;
  color: #475569;
  font-size: 14px;
  line-height: 1.8;
}

.summary-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 18px;
}

.summary-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 150px;
  padding: 14px 18px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #fff;
}

.summary-value {
  font-size: 22px;
  font-weight: 700;
  color: #111827;
}

.summary-label {
  font-size: 12px;
  color: #64748b;
}

.block {
  margin-bottom: 32px;
  padding: 22px 24px;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #fff;
}

h2 {
  margin: 0 0 10px;
  font-size: 18px;
  color: #111827;
}

.timeline {
  display: grid;
  gap: 10px;
  margin-top: 18px;
}

.timeline-node {
  display: flex;
  gap: 16px;
  padding: 12px 14px;
  border-left: 3px solid #cbd5e1;
  border-radius: 6px;
  background: #f8fafc;
}

.timeline-node.confidence-known {
  border-left-color: #2563eb;
}

.timeline-node.confidence-derived {
  border-left-color: #d97706;
}

.timeline-node.confidence-unknown {
  border-left-color: #94a3b8;
  border-left-style: dashed;
}

.timeline-range {
  flex: 0 0 150px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  color: #475569;
}

.timeline-body {
  flex: 1;
  min-width: 0;
}

.timeline-title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.stage-tag {
  padding: 1px 8px;
  border-radius: 4px;
  background: #e2e8f0;
  font-size: 12px;
  color: #334155;
}

.org-name {
  font-size: 15px;
  font-weight: 600;
  color: #111827;
}

.timeline-basis {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: #64748b;
}

.confidence-tag,
.sensitivity-tag,
.status-tag {
  padding: 1px 8px;
  border-radius: 4px;
  font-size: 12px;
  white-space: nowrap;
}

.confidence-known {
  background: #dbeafe;
  color: #1d4ed8;
}

.confidence-derived {
  background: #fef3c7;
  color: #b45309;
}

.confidence-unknown {
  background: #e2e8f0;
  color: #475569;
}

.sensitivity-low {
  background: #e2e8f0;
  color: #475569;
}

.sensitivity-medium {
  background: #fef3c7;
  color: #b45309;
}

.sensitivity-high {
  background: #fee2e2;
  color: #b91c1c;
}

.status-todo {
  background: #e2e8f0;
  color: #475569;
}

.status-running {
  background: #dbeafe;
  color: #1d4ed8;
}

.status-done {
  background: #dcfce7;
  color: #15803d;
}

.status-skipped {
  background: #f1f5f9;
  color: #94a3b8;
}

.note-line {
  margin: 16px 0 0;
  font-size: 13px;
  line-height: 1.8;
  color: #475569;
}

.data-table {
  width: 100%;
  margin-top: 16px;
  border-collapse: collapse;
  font-size: 13px;
}

.data-table th,
.data-table td {
  padding: 9px 10px;
  border-bottom: 1px solid #eef2f7;
  text-align: left;
  vertical-align: top;
}

.data-table th {
  background: #f8fafc;
  font-weight: 600;
  color: #334155;
}

.value-cell {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: #1f2937;
}

.note-cell {
  color: #64748b;
}

.empty-state {
  margin: 16px 0 0;
  padding: 18px;
  border: 1px dashed #cbd5e1;
  border-radius: 8px;
  background: #f8fafc;
  font-size: 13px;
  color: #64748b;
}

.boundary-list {
  margin: 12px 0 0;
  padding-left: 20px;
  font-size: 13px;
  line-height: 1.9;
  color: #475569;
}

@media (max-width: 768px) {
  .footprint-page {
    padding: 20px;
  }

  .timeline-node {
    flex-direction: column;
    gap: 6px;
  }

  .timeline-range {
    flex: none;
  }

  .data-table {
    display: block;
    overflow-x: auto;
  }
}
</style>
