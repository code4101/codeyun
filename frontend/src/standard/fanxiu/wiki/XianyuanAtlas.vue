<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useResizablePane } from '@/utils/useResizablePane'
import {
  collectFanxiuXianyuanAtlas,
  getFanxiuXianyuanAtlas,
  type FanxiuXianyuanAtlasSnapshot,
} from '@/api/fanxiu'
import FanxiuActivityUpdateButton from '../components/FanxiuActivityUpdateButton.vue'

const snapshot = ref<FanxiuXianyuanAtlasSnapshot | null>(null)
const loading = ref(false)
const collecting = ref(false)
const keyword = ref('')
const selectedId = ref(0)
const atlasRef = ref<HTMLElement | null>(null)

function splitPaneBounds() {
  const containerHeight = atlasRef.value?.clientHeight || Math.max(620, window.innerHeight - 220)
  const availableHeight = Math.max(480, containerHeight - 54)
  return {
    adaptiveHeight: Math.max(220, Math.floor(availableHeight * 0.46)),
    maxHeight: Math.max(260, availableHeight - 260),
  }
}

const {
  paneHeight: listPaneHeight,
  isResizing,
  startResizing,
} = useResizablePane({
  initialHeight: 360,
  getAdaptiveHeight: () => splitPaneBounds().adaptiveHeight,
  getResizeBounds: () => ({
    min: 180,
    max: splitPaneBounds().maxHeight,
  }),
  storageKey: 'fanxiu:wiki:xianyuan-list-pane-height',
})

const listPaneStyle = computed(() => ({ height: `${listPaneHeight.value}px` }))

const people = computed(() => snapshot.value?.people || [])
const targetGongfa = computed(() => snapshot.value?.target_gongfa || null)
const targetName = computed(() => targetGongfa.value?.name || '悟境')
const searchedPeople = computed(() => {
  const query = keyword.value.trim().toLocaleLowerCase()
  return people.value.filter(person => (
    !query || [
      person.name,
      person.npc_id,
      ...person.reward_kinds,
      ...person.rewards.map(item => item.name),
      ...(person.target_rewards || []).map(item => item.name),
      ...(person.selectable_rewards || []).flatMap(item => [
        item.name,
        ...(item.optional_items || []).map(option => option.name),
      ]),
      ...(person.hobby_groups || []).map(item => item.name),
      ...(person.gift_options || []).map(item => item.name),
    ]
      .some(value => String(value).toLocaleLowerCase().includes(query))
  ))
})
const giftablePeople = computed(() => searchedPeople.value.filter(person => person.giftable && !person.hostile))
const hostilePeople = computed(() => searchedPeople.value.filter(person => person.hostile))
const visiblePeople = computed(() => [...giftablePeople.value, ...hostilePeople.value])
const selected = computed(() => (
  people.value.find(person => person.npc_id === selectedId.value) || visiblePeople.value[0] || null
))
const updatedAt = computed(() => {
  const value = Number(snapshot.value?.runtime_updated_at || 0)
  return value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '尚未更新'
})

function selectFirst() {
  if (!visiblePeople.value.some(person => person.npc_id === selectedId.value)) {
    selectedId.value = visiblePeople.value[0]?.npc_id || 0
  }
}

function itemHref(itemId: number) {
  return `/standalone/fanxiu/wiki?tab=item&id=${itemId}`
}

function formatNumber(value: number | null | undefined) {
  return value == null ? '—' : Number(value).toLocaleString('zh-CN')
}

function resetCostSummary(person: FanxiuXianyuanAtlasSnapshot['people'][number]) {
  const costs = (person.reset_steps || []).map(step => step.favor_cost).filter(cost => cost > 0)
  if (!costs.length) return '—'
  const minimum = Math.min(...costs)
  const maximum = Math.max(...costs)
  return minimum === maximum ? formatNumber(minimum) : `${formatNumber(minimum)}–${formatNumber(maximum)}`
}

async function load() {
  loading.value = true
  try {
    snapshot.value = await getFanxiuXianyuanAtlas()
    selectFirst()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '读取仙缘图鉴失败')
  } finally {
    loading.value = false
  }
}

async function collect() {
  collecting.value = true
  try {
    snapshot.value = await collectFanxiuXianyuanAtlas()
    selectFirst()
    ElMessage.success(`已更新 ${snapshot.value.runtime_item_count} 位已开放仙缘`)
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || error?.message || '更新仙缘图鉴失败')
  } finally {
    collecting.value = false
  }
}

onMounted(load)
</script>

<template>
  <section ref="atlasRef" class="atlas-page" v-loading="loading">
    <header class="toolbar">
      <span class="updated-at">{{ updatedAt }}</span>
      <div class="actions">
        <el-input v-model="keyword" clearable size="small" placeholder="搜索人物或储物" @input="selectFirst" />
        <FanxiuActivityUpdateButton :visible="true" :loading="collecting" :disabled="loading" @collect="collect" />
      </div>
    </header>

    <div class="body" :class="{ 'is-resizing': isResizing }">
      <div class="list-pane" :style="listPaneStyle">
        <div v-if="visiblePeople.length" class="relation-tables">
          <section class="relation-table giftable-table">
            <h3>
              可送礼
              <span v-if="targetGongfa">当前专项：第 {{ targetGongfa.upgrade_index }} 本 · {{ targetName }}悟境</span>
            </h3>
            <div class="relation-table-body">
              <el-table
                class="people-table"
                :data="giftablePeople"
                height="100%"
                size="small"
                row-key="npc_id"
                table-layout="auto"
                :fit="false"
                highlight-current-row
                :current-row-key="selectedId"
                @row-click="selectedId = $event.npc_id"
              >
                <el-table-column prop="name" label="仙缘" min-width="130" />
                <el-table-column label="当前" width="68" align="right">
                  <template #default="{ row }">{{ row.favor_level }} 级</template>
                </el-table-column>
                <el-table-column label="重置段" width="70" align="right">
                  <template #default="{ row }">{{ row.reset_steps?.length || 0 }}</template>
                </el-table-column>
                <el-table-column label="段成本" width="112" align="right">
                  <template #default="{ row }">{{ resetCostSummary(row) }}</template>
                </el-table-column>
                <el-table-column label="自选匣" width="70" align="right">
                  <template #default="{ row }">{{ row.selectable_reward_count || 0 }}</template>
                </el-table-column>
                <el-table-column label="悟境匣" width="70" align="right">
                  <template #default="{ row }">{{ row.wujing_selectable_reward_count || 0 }}</template>
                </el-table-column>
                <el-table-column label="专项均价" width="102" align="right">
                  <template #default="{ row }">{{ formatNumber(row.target_average_wujing_cost) }}</template>
                </el-table-column>
                <el-table-column width="28" class-name="table-tail-space" label-class-name="table-tail-space" />
              </el-table>
            </div>
          </section>

          <section class="relation-table hostile-table">
            <h3>敌对</h3>
            <div class="relation-table-body">
              <el-table
                class="people-table"
                :data="hostilePeople"
                height="100%"
                size="small"
                row-key="npc_id"
                table-layout="auto"
                :fit="false"
                highlight-current-row
                :current-row-key="selectedId"
                @row-click="selectedId = $event.npc_id"
              >
                <el-table-column prop="name" label="仙缘" min-width="130" />
              </el-table>
            </div>
          </section>
        </div>
        <el-empty v-else description="尚无匹配仙缘" :image-size="72" />
      </div>

      <div
        class="pane-resizer"
        role="separator"
        aria-orientation="horizontal"
        title="拖动调整列表和详情的比例"
        @mousedown="startResizing"
      >
        <span></span>
      </div>

      <main class="detail-pane">
        <template v-if="selected">
          <div class="detail-heading">
            <h2>{{ selected.name }}</h2>
            <span>#{{ selected.npc_id }}</span>
            <span v-if="selected.target_recommendation_rank === 1" class="recommended">首选</span>
          </div>
          <div class="facts">
            <span>好感等级 {{ selected.favor_level }}</span>
            <span v-if="selected.reset_favor_level">重置进度 {{ selected.reset_favor_level }} 级</span>
            <span v-if="selected.target_best_reset_step != null">推荐重置第 {{ selected.target_best_reset_step }} 段</span>
            <span v-if="selected.target_cycle_favor_cost != null">该段 {{ formatNumber(selected.target_cycle_favor_cost) }} 好感</span>
            <span v-if="selected.target_cycle_reward_count">该段可得 {{ selected.target_cycle_reward_count }} 个悟境匣</span>
            <span v-if="selected.target_average_wujing_cost != null">平均 {{ formatNumber(selected.target_average_wujing_cost) }} 好感/个</span>
            <span>可赠 {{ selected.gift_option_count || 0 }} 种</span>
            <span v-if="selected.activity_flower_gift_count">瑶池仙花 {{ selected.activity_flower_gift_count }} 种</span>
            <span>自选匣 {{ selected.selectable_reward_count || 0 }} 个</span>
            <span>悟境匣 {{ selected.wujing_selectable_reward_count || 0 }} 个</span>
          </div>
          <section>
            <h3>可赠礼物</h3>
            <p v-if="selected.gift_restriction" class="restriction">{{ selected.gift_restriction }}</p>
            <el-table
              v-if="selected.gift_options?.length"
              class="detail-table"
              :data="selected.gift_options"
              size="small"
              row-key="item_id"
              table-layout="auto"
              :fit="false"
            >
              <el-table-column prop="hobby_name" label="喜好" width="92" />
              <el-table-column prop="name" label="礼物" min-width="180">
                <template #default="{ row }">
                  <a :href="itemHref(row.item_id)" target="_blank" rel="noopener">{{ row.name }}</a>
                  <el-tag v-if="row.activity_gift" class="gift-tag" size="small" effect="plain">瑶池仙花</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="favorability" label="好感" width="72" align="right" />
              <el-table-column label="限制" width="104">
                <template #default="{ row }">{{ row.career_conditional ? '流派条件' : '可赠' }}</template>
              </el-table-column>
            </el-table>
            <el-empty v-else :description="selected.no_gift_description || '该人物没有可赠礼物配置'" :image-size="64" />
          </section>
          <section>
            <h3>自选匣</h3>
            <el-table
              v-if="selected.selectable_rewards?.length"
              class="detail-table selectable-table"
              :data="selected.selectable_rewards"
              size="small"
              row-key="reward_key"
              table-layout="auto"
              :fit="false"
            >
              <el-table-column prop="level" label="档" width="50" align="right" />
              <el-table-column prop="name" label="自选匣" min-width="150">
                <template #default="{ row }">
                  <a :href="itemHref(row.item_id)" target="_blank" rel="noopener">{{ row.name }}</a>
                  <span v-if="row.count > 1"> ×{{ row.count }}</span>
                </template>
              </el-table-column>
              <el-table-column label="可选内容" min-width="420">
                <template #default="{ row }">
                  <span v-for="(option, index) in row.optional_items" :key="option.item_id">
                    <span v-if="index">、</span>
                    <a :href="itemHref(option.item_id)" target="_blank" rel="noopener">{{ option.name }}</a>
                  </span>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-else description="该人物没有自选匣奖励" :image-size="64" />
          </section>
          <section v-if="selected.target_rewards?.length">
            <h3>对 {{ targetName }} 悟境有用</h3>
            <el-table
              v-if="selected.target_rewards?.length"
              class="detail-table"
              :data="selected.target_rewards"
              size="small"
              row-key="reward_key"
              table-layout="auto"
              :fit="false"
            >
              <el-table-column prop="level" label="档" width="50" align="right" />
              <el-table-column prop="name" label="奖励" min-width="180">
                <template #default="{ row }">
                  <a :href="itemHref(row.item_id)" target="_blank" rel="noopener">{{ row.name }}</a>
                  <span v-if="row.count > 1"> ×{{ row.count }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="target_support_mode" label="方式" width="72" />
            </el-table>
          </section>
        </template>
        <el-empty v-else description="选择一位仙缘查看详情" :image-size="72" />
      </main>
    </div>
  </section>
</template>

<style scoped>
.atlas-page { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
.toolbar { min-height: 54px; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 8px 14px; border-bottom: 1px solid var(--el-border-color-light); }
.actions, .detail-heading, .facts { display: flex; align-items: center; gap: 10px; }
.detail-heading h2, section h3 { margin: 0; }
.updated-at, .detail-heading span, .facts { color: var(--el-text-color-secondary); font-size: 12px; white-space: nowrap; }
.actions :deep(.el-input) { width: 190px; }
.body { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
.list-pane { flex: none; min-height: 0; padding: 10px 14px 0; overflow: hidden; }
.relation-tables { height: 100%; min-height: 0; display: grid; grid-template-columns: minmax(570px, 1fr) minmax(180px, 240px); gap: 18px; overflow: hidden; }
.relation-table { min-width: 0; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
.relation-table h3 { flex: none; margin: 0 0 6px; color: var(--el-text-color-primary); font-size: 14px; line-height: 22px; }
.relation-table h3 span { margin-left: 8px; color: var(--el-text-color-secondary); font-size: 12px; font-weight: 400; }
.hostile-table h3 { color: var(--el-color-danger); }
.relation-table-body { flex: 1; min-height: 0; overflow: hidden; }
.people-table, .detail-table { width: max-content; max-width: 100%; }
.pane-resizer { flex: none; height: 12px; display: flex; align-items: center; justify-content: center; cursor: ns-resize; touch-action: none; border-bottom: 1px solid var(--el-border-color-light); }
.pane-resizer span { width: 48px; height: 4px; border-top: 1px solid var(--el-border-color); border-bottom: 1px solid var(--el-border-color); }
.pane-resizer:hover, .body.is-resizing .pane-resizer { background: var(--el-color-primary-light-9); }
.pane-resizer:hover span, .body.is-resizing .pane-resizer span { border-color: var(--el-color-primary); }
.detail-pane { flex: 1; min-height: 0; padding: 14px; overflow: auto; }
.detail-heading { align-items: baseline; margin-bottom: 8px; }
.detail-heading h2 { font-size: 20px; }
.detail-heading .recommended { color: var(--el-color-success); }
.facts { flex-wrap: wrap; padding-bottom: 12px; border-bottom: 1px solid var(--el-border-color-lighter); }
section h3 { margin: 14px 0 8px; color: #6f4b16; font-size: 14px; }
a { color: var(--el-color-primary); text-decoration: none; }
.restriction { margin: 0 0 8px; color: var(--el-color-warning-dark-2); font-size: 12px; }
.gift-tag { margin-left: 8px; }
.selectable-table :deep(.cell) { white-space: normal; }
@media (max-width: 900px) { .toolbar { align-items: stretch; flex-direction: column; } .relation-tables { grid-template-columns: minmax(520px, 1fr) minmax(200px, 240px); overflow-x: auto; } }
</style>
