<template>
  <div class="ai-agents-page">
    <header class="page-header">
      <div>
        <div class="eyebrow">AI工具 / AGENTS</div>
        <h1>AGENTS</h1>
      </div>
      <el-button text @click="copyAgentsMarkdown">
        复制原文
      </el-button>
    </header>

    <main class="content-shell">
      <article class="markdown-body" v-html="agentsHtml"></article>
    </main>
  </div>
</template>

<script setup lang="ts">
import DOMPurify from 'dompurify'
import { ElMessage } from 'element-plus'
import { marked } from 'marked'
import agentsMarkdown from '../../../../../AGENTS.md?raw'

const agentsHtml = DOMPurify.sanitize(marked.parse(agentsMarkdown, { async: false }))

const copyAgentsMarkdown = async () => {
  await navigator.clipboard.writeText(agentsMarkdown)
  ElMessage.success('已复制 AGENTS.md')
}
</script>

<style scoped>
.ai-agents-page {
  min-height: 100%;
  padding: 24px 28px 40px;
  background: #f6f8fb;
  color: #1f2937;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  max-width: 1040px;
  margin: 0 auto 16px;
}

.eyebrow {
  margin-bottom: 4px;
  color: #64748b;
  font-size: 13px;
}

.page-header h1 {
  margin: 0;
  color: #111827;
  font-size: 28px;
  line-height: 1.25;
}

.content-shell {
  max-width: 1040px;
  margin: 0 auto;
  padding: 22px 28px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
}

.markdown-body {
  font-size: 14px;
  line-height: 1.75;
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  color: #111827;
  line-height: 1.35;
}

.markdown-body :deep(h1) {
  margin: 0 0 18px;
  font-size: 24px;
}

.markdown-body :deep(h2) {
  margin: 24px 0 10px;
  padding-top: 18px;
  border-top: 1px solid #eef2f7;
  font-size: 18px;
}

.markdown-body :deep(h3) {
  margin: 18px 0 8px;
  font-size: 15px;
}

.markdown-body :deep(p),
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 8px 0;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 22px;
}

.markdown-body :deep(li + li) {
  margin-top: 4px;
}

.markdown-body :deep(code) {
  padding: 1px 5px;
  border-radius: 4px;
  background: #f1f5f9;
  color: #0f172a;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace;
  font-size: 0.92em;
}

.markdown-body :deep(pre) {
  overflow: auto;
  padding: 12px 14px;
  border-radius: 6px;
  background: #0f172a;
  color: #e5e7eb;
}

.markdown-body :deep(pre code) {
  padding: 0;
  background: transparent;
  color: inherit;
}

@media (max-width: 760px) {
  .ai-agents-page {
    padding: 18px 14px 28px;
  }

  .page-header {
    align-items: stretch;
    flex-direction: column;
  }

  .content-shell {
    padding: 18px;
  }
}
</style>
