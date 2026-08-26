<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  getLatestFanxiuExchangeActivitySnapshot,
  type FanxiuExchangeActivitySnapshot,
} from '@/api/fanxiu'

type TopActivityType = 'yunmeng-trial' | 'xianyuan-duokui' | 'xutian-palace' | 'magic-invasion' | 'beast-abyss'

type ActivityOption = {
  label: string
  value: TopActivityType
}

const activityOptions: ActivityOption[] = [
  {
    label: '云梦试剑',
    value: 'yunmeng-trial',
  },
  {
    label: '仙缘夺魁',
    value: 'xianyuan-duokui',
  },
  {
    label: '虚天殿',
    value: 'xutian-palace',
  },
  {
    label: '魔道入侵',
    value: 'magic-invasion',
  },
  {
    label: '兽渊探秘',
    value: 'beast-abyss',
  },
]
const activityTypes = new Set<TopActivityType>(activityOptions.map(item => item.value))
const route = useRoute()
const router = useRouter()
const XutianPalacePage = defineAsyncComponent(() => import('../xutian-palace/page.vue'))
const MagicInvasionPage = defineAsyncComponent(() => import('../magic-invasion/page.vue'))
const BeastAbyssPage = defineAsyncComponent(() => import('../beast-abyss/page.vue'))
const resolvedDefaultType = ref<TopActivityType | null>(null)
const initialSnapshot = ref<FanxiuExchangeActivitySnapshot | null>(null)

function isActivityType(value: unknown): value is TopActivityType {
  return activityTypes.has(String(value || '') as TopActivityType)
}

const selectedType = computed<TopActivityType>({
  get() {
    const value = String(route.query.activity || '') as TopActivityType
    return isActivityType(value) ? value : (resolvedDefaultType.value ?? activityOptions[0].value)
  },
  set(value) {
    void router.replace({
      query: {
        ...route.query,
        activity: value,
      },
    })
  },
})
const activePage = computed(() => {
  if (!isActivityType(route.query.activity) && !resolvedDefaultType.value) return null
  if (selectedType.value === 'magic-invasion') return MagicInvasionPage
  if (selectedType.value === 'beast-abyss') return BeastAbyssPage
  return XutianPalacePage
})
const selectedActivityName = computed(() => (
  activityOptions.find(item => item.value === selectedType.value)?.label ?? '玩法榜'
))

watch(
  () => route.query.activity,
  async value => {
    if (isActivityType(value)) return
    const latest = await getLatestFanxiuExchangeActivitySnapshot(
      activityOptions.map(item => item.value),
    )
    if (isActivityType(route.query.activity)) return
    const latestType = isActivityType(latest.activity_type)
      ? latest.activity_type
      : activityOptions[0].value
    resolvedDefaultType.value = latestType
    initialSnapshot.value = latest.activity_type === latestType
      ? (latest.snapshot ?? null)
      : null
  },
  { immediate: true },
)
</script>

<template>
  <div class="top-activity-page">
    <header class="page-header">
      <h2>玩法榜</h2>
    </header>

    <component
      :is="activePage"
      v-if="activePage"
      embedded
      :initial-snapshot="initialSnapshot ?? undefined"
      :activity-type="['yunmeng-trial', 'xianyuan-duokui'].includes(selectedType) ? selectedType : undefined"
      :activity-name="['yunmeng-trial', 'xianyuan-duokui'].includes(selectedType) ? selectedActivityName : undefined"
    >
      <template #activity-type-control>
        <el-select v-model="selectedType" class="activity-type-select" aria-label="选择活动类型">
          <el-option
            v-for="item in activityOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </template>
    </component>
  </div>
</template>

<style scoped>
.top-activity-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 20px;
}

.page-header h2 {
  margin: 0;
}

.activity-type-select {
  width: 150px;
}
</style>
