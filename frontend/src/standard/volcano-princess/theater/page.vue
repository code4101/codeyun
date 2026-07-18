<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import {
  getVolcanoPrincessTheaterCatalog,
  type VolcanoPrincessTheaterCatalog,
  type VolcanoPrincessTheaterDrama,
} from '@/api/volcanoPrincess'

type CatalogMode = 'questions' | 'dramas' | 'scenes'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const catalog = ref<VolcanoPrincessTheaterCatalog | null>(null)
const initialMode = route.query.view === 'dramas' || route.query.view === 'scenes'
  ? route.query.view
  : 'questions'
const mode = ref<CatalogMode>(initialMode)
const query = ref('')
const dramaCategory = ref('')

const modeOptions = [
  { label: '台词题库', value: 'questions' },
  { label: '剧目资料', value: 'dramas' },
  { label: '场景素材', value: 'scenes' },
]

const questionGroupOrder = ['希望', '绝望', '平静', '愤怒', '浪漫']

const filteredQuestions = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase()
  return (catalog.value?.questions ?? []).filter((item) => (
    !needle || item.content.toLocaleLowerCase().includes(needle) || item.line_type.includes(needle)
  ))
})

const questionGroups = computed(() => (catalog.value?.line_types ?? [])
  .map((line) => ({
    ...line,
    questions: filteredQuestions.value.filter((item) => item.line_type_index === line.index),
  }))
  .filter((group) => group.questions.length > 0)
  .sort((left, right) => (
    questionGroupOrder.indexOf(left.name) - questionGroupOrder.indexOf(right.name)
  )))

const filteredDramas = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase()
  return (catalog.value?.dramas ?? []).filter((item) => (
    (!dramaCategory.value || item.category === dramaCategory.value)
    && (!needle || [item.name, item.description, item.role, item.category]
      .some((value) => value.toLocaleLowerCase().includes(needle)))
  ))
})

function changeMode(value: string | number | boolean) {
  mode.value = value === 'dramas' || value === 'scenes' ? value : 'questions'
  query.value = ''
  dramaCategory.value = ''
  void router.replace({
    path: route.path,
    query: mode.value === 'questions' ? {} : { view: mode.value },
  })
}

function formatRequirements(drama: VolcanoPrincessTheaterDrama): string {
  return drama.requirements.map((item) => `${item.nature} ${item.value}`).join('、') || '无'
}

async function loadCatalog() {
  loading.value = true
  try {
    catalog.value = await getVolcanoPrincessTheaterCatalog()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '奥拉夫剧院图鉴加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(loadCatalog)
</script>

<template>
  <main class="theater-catalog-page" v-loading="loading">
    <header class="page-head">
      <div>
        <h1>奥拉夫剧院图鉴</h1>
        <p v-if="catalog">
          {{ catalog.summary.question_count }} 道台词题 · {{ catalog.summary.drama_count }} 个剧目
          <template v-if="catalog.source.build_id"> · Steam Build {{ catalog.source.build_id }}</template>
        </p>
      </div>
      <el-segmented v-model="mode" :options="modeOptions" @change="changeMode" />
    </header>

    <p v-if="catalog && mode !== 'scenes'" class="mechanics-note">
      每次演出进行 {{ catalog.mechanics.rounds }} 轮，每轮随机出现 {{ catalog.mechanics.options_per_round }} 句台词；
      选择符合指定情绪的台词即为正确。消耗 {{ catalog.mechanics.energy_cost }} 点体力，演出 BGM 为
      “{{ catalog.mechanics.performance_bgm_name }}”。
    </p>

    <section v-if="mode !== 'scenes'" class="catalog-toolbar">
      <el-input
        v-model="query"
        class="search-input"
        clearable
        :prefix-icon="Search"
        :placeholder="mode === 'questions' ? '搜索台词或情绪' : '搜索剧名、类型、角色或描述'"
      />
      <el-select v-if="mode === 'dramas'" v-model="dramaCategory" class="filter-select">
        <el-option value="" label="全部类型" />
        <el-option
          v-for="item in catalog?.drama_categories ?? []"
          :key="item"
          :value="item"
          :label="item"
        />
      </el-select>
      <span v-if="query.trim() || dramaCategory" class="result-count">
        {{ mode === 'questions' ? filteredQuestions.length : filteredDramas.length }} 条
      </span>
    </section>

    <section v-if="mode === 'scenes'" class="scene-gallery">
      <figure v-for="image in catalog?.images ?? []" :key="image.id" class="scene-figure">
        <img
          :src="image.media_url"
          :alt="image.title"
          :width="image.width"
          :height="image.height"
        >
        <figcaption>
          <strong>{{ image.title }}</strong>
          <span>{{ image.description }}</span>
        </figcaption>
      </figure>
      <div v-if="!catalog?.images?.length && !loading" class="card-empty">尚未找到剧院场景素材</div>
    </section>

    <section v-else-if="mode === 'questions'" class="question-card-scroll">
      <div v-if="questionGroups.length" class="question-grid">
        <article
          v-for="group in questionGroups"
          :key="group.index"
          class="question-card"
          :class="`tone-card-${group.index}`"
        >
          <header class="question-card-head">
            <h2>{{ group.name }}</h2>
            <span>{{ group.questions.length }} 句</span>
          </header>
          <ol class="question-list">
            <li v-for="item in group.questions" :key="item.index">
              <span class="question-number">{{ item.line_index + 1 }}</span>
              <span class="question-content">{{ item.content }}</span>
            </li>
          </ol>
        </article>
      </div>
      <div v-else-if="!loading" class="card-empty">没有符合条件的台词题</div>
    </section>

    <section v-else class="table-wrap">
      <table class="drama-table">
        <colgroup>
          <col class="number-col">
          <col class="level-col">
          <col class="name-col">
          <col class="compact-col">
          <col class="compact-col">
          <col class="requirement-col">
          <col class="number-value-col">
          <col class="number-value-col">
          <col class="number-value-col">
          <col class="description-col">
        </colgroup>
        <thead>
          <tr>
            <th>#</th><th>演技级别</th><th>剧目</th><th>类型</th><th>角色</th><th>属性要求</th>
            <th>魅力</th><th>基础报酬</th><th>声望</th><th>简介</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in filteredDramas" :key="item.index">
            <td class="mono">{{ item.index + 1 }}</td>
            <td class="mono">Lv.{{ item.theater_level }}</td>
            <td class="drama-name">{{ item.name }}</td>
            <td><span class="category-label">{{ item.category }}</span></td>
            <td>{{ item.role }}</td>
            <td>{{ formatRequirements(item) }}</td>
            <td class="mono">{{ item.charm }}</td>
            <td class="mono">{{ item.base_salary }}</td>
            <td class="mono">{{ item.fame }}</td>
            <td class="drama-description">{{ item.description }}</td>
          </tr>
          <tr v-if="!filteredDramas.length && !loading">
            <td colspan="10" class="empty-row">没有符合条件的剧目</td>
          </tr>
        </tbody>
      </table>
    </section>
  </main>
</template>

<style scoped>
.theater-catalog-page {
  box-sizing: border-box;
  height: 100%;
  min-height: 0;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: hidden;
  color: #202124;
}

.page-head {
  flex: none;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

.page-head h1 {
  margin: 0;
  font-size: 22px;
  line-height: 1.35;
}

.page-head p {
  margin: 3px 0 0;
  color: #6b7280;
  font-size: 13px;
}

.mechanics-note {
  flex: none;
  margin: 0;
  padding: 8px 10px;
  border-left: 3px solid #8fb4ed;
  background: #f5f8fd;
  color: #566170;
  font-size: 13px;
}

.catalog-toolbar {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
}

.search-input {
  width: 340px;
}

.filter-select {
  width: 150px;
}

.result-count {
  color: #7a8390;
  font-size: 13px;
}

.table-wrap {
  min-height: 0;
  flex: 1;
  overflow: auto;
  border: 1px solid #d8dde6;
  border-radius: 6px;
  background: #fff;
}

table {
  width: 100%;
  border-collapse: collapse;
  background: #fff;
}

th,
td {
  padding: 8px 10px;
  border-bottom: 1px solid #edf0f4;
  text-align: left;
  font-size: 13px;
  white-space: nowrap;
}

th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: #f7f8fa;
  font-weight: 600;
}

tbody tr:hover {
  background: #f4f8ff;
}

col.number-col,
col.level-col,
col.compact-col,
col.requirement-col,
col.number-value-col,
col.name-col {
  width: 1%;
}

col.description-col {
  width: auto;
}

.mono {
  font-variant-numeric: tabular-nums;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}

.category-label {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  background: #f1f3f5;
  color: #4b5563;
  font-size: 12px;
}

.question-card-scroll {
  min-height: 0;
  flex: 1;
  overflow: auto;
}

.scene-gallery {
  min-height: 0;
  flex: 1;
  overflow: auto;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  gap: 12px;
  align-content: start;
}

.scene-figure {
  margin: 0;
  min-width: 0;
  overflow: hidden;
  border: 1px solid #d8dde6;
  border-radius: 6px;
  background: #fff;
}

.scene-figure img {
  display: block;
  width: 100%;
  height: auto;
  aspect-ratio: 1223 / 656;
  object-fit: contain;
  background: #2a1d18;
}

.scene-figure figcaption {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 8px 10px;
  font-size: 13px;
}

.scene-figure figcaption span {
  color: #737b87;
}

.question-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  align-items: start;
}

.question-card {
  --tone-color: #667085;
  --tone-bg: #f4f5f7;
  overflow: hidden;
  border: 1px solid #dfe3e9;
  border-top: 3px solid var(--tone-color);
  border-radius: 6px;
  background: #fff;
}

.tone-card-0 { --tone-color: #c75b54; --tone-bg: #fbe9e8; }
.tone-card-1 { --tone-color: #bd8b14; --tone-bg: #fff4cc; }
.tone-card-2 { --tone-color: #469661; --tone-bg: #e6f4ea; }
.tone-card-3 { --tone-color: #bd5d8e; --tone-bg: #f9e8f1; }
.tone-card-4 { --tone-color: #78838e; --tone-bg: #eceff2; }

.question-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  background: var(--tone-bg);
}

.question-card-head h2 {
  margin: 0;
  color: var(--tone-color);
  font-size: 15px;
  line-height: 1.35;
}

.question-card-head span {
  color: #7a8390;
  font-size: 12px;
}

.question-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.question-list li {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  gap: 7px;
  padding: 8px 10px;
  border-bottom: 1px solid #edf0f4;
  font-size: 13px;
  line-height: 1.5;
}

.question-list li:last-child {
  border-bottom: 0;
}

.question-list li:hover {
  background: #f8faff;
}

.question-number {
  color: #9299a4;
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}

.question-content {
  min-width: 0;
  overflow-wrap: anywhere;
}

.card-empty {
  height: 120px;
  display: grid;
  place-items: center;
  border: 1px solid #d8dde6;
  border-radius: 6px;
  background: #fff;
  color: #8a919e;
  font-size: 13px;
}

.drama-name {
  font-weight: 600;
}

.drama-description {
  min-width: 420px;
  max-width: 720px;
  white-space: normal;
  line-height: 1.5;
}

.empty-row {
  height: 90px;
  color: #8a919e;
  text-align: center;
}

@media (max-width: 760px) {
  .theater-catalog-page { padding: 10px; }
  .page-head { align-items: flex-start; flex-direction: column; }
  .catalog-toolbar { flex-wrap: wrap; }
  .search-input { width: 100%; }
  .mechanics-note { line-height: 1.55; }
  .question-grid { grid-template-columns: minmax(0, 1fr); }
  .scene-gallery { grid-template-columns: minmax(0, 1fr); }
  .scene-figure figcaption { align-items: flex-start; flex-direction: column; gap: 2px; }
}
</style>
