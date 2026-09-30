<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  getLatestFanxiuExchangeActivitySnapshot,
  type FanxiuExchangeActivitySnapshot,
} from '@/api/fanxiu/activities';

type TopActivityType = 'yunmeng-trial' | 'xianyuan-duokui' | 'xutian-palace' | 'magic-invasion' | 'beast-abyss' | 'tiandi-yiju' | 'shengxian-hui'

type ActivityOption = {
  label: string
  value: TopActivityType
}

const props = withDefaults(defineProps<{
  embedded?: boolean
  initialActivityType?: TopActivityType | null
  initialSnapshot?: FanxiuExchangeActivitySnapshot | null
}>(), {
  embedded: false,
  initialActivityType: null,
  initialSnapshot: null,
})

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
  {
    label: '天地弈局',
    value: 'tiandi-yiju',
  },
  { label: '升仙会', value: 'shengxian-hui' },
]
const activityTypes = new Set<TopActivityType>(activityOptions.map(item => item.value))
const route = useRoute()
const router = useRouter()
const XutianPalacePage = defineAsyncComponent(() => import('../xutian-palace/page.vue'))
const MagicInvasionPage = defineAsyncComponent(() => import('../magic-invasion/page.vue'))
const BeastAbyssPage = defineAsyncComponent(() => import('../beast-abyss/page.vue'))
const ShengxianHuiPage = defineAsyncComponent(() => import('../shengxian-hui/page.vue'))
const resolvedDefaultType = ref<TopActivityType | null>(null)
const resolvedInitialSnapshot = ref<FanxiuExchangeActivitySnapshot | null>(null)
const embeddedType = ref<TopActivityType | null>(null)

function isActivityType(value: unknown): value is TopActivityType {
  return activityTypes.has(String(value || '') as TopActivityType)
}

const selectedType = computed<TopActivityType>({
  get() {
    if (props.embedded) {
      return embeddedType.value ?? props.initialActivityType ?? activityOptions[0].value
    }
    const value = String(route.query.activity || '') as TopActivityType
    return isActivityType(value) ? value : (resolvedDefaultType.value ?? activityOptions[0].value)
  },
  set(value) {
    if (props.embedded) {
      embeddedType.value = value
      return
    }
    void router.replace({
      query: {
        ...route.query,
        activity: value,
      },
    })
  },
})
const activePage = computed(() => {
  if (props.embedded && !isActivityType(selectedType.value)) return null
  if (!props.embedded && !isActivityType(route.query.activity) && !resolvedDefaultType.value) return null
  if (selectedType.value === 'magic-invasion') return MagicInvasionPage
  if (selectedType.value === 'beast-abyss') return BeastAbyssPage
  if (selectedType.value === 'shengxian-hui') return ShengxianHuiPage
  return XutianPalacePage
})
const selectedActivityName = computed(() => (
  activityOptions.find(item => item.value === selectedType.value)?.label ?? '玩法榜'
))
const activePageProps = computed(() => {
  if (selectedType.value === 'magic-invasion' || selectedType.value === 'beast-abyss' || selectedType.value === 'shengxian-hui') {
    return {}
  }
  return {
    activityType: selectedType.value,
    activityName: selectedActivityName.value,
    ...(selectedType.value === 'tiandi-yiju'
      ? {
          comparativeRankingScope: 'alliance',
          comparativeRankingTitle: '宗门/位面排名',
          comparativeRankingSubjectLabel: '宗门/位面',
        }
      : {}),
  }
})
const selectedInitialSnapshot = computed(() => (
  props.embedded
    ? (selectedType.value === props.initialActivityType ? (props.initialSnapshot ?? undefined) : undefined)
    : (!isActivityType(route.query.activity)
  && resolvedDefaultType.value === selectedType.value
      ? (resolvedInitialSnapshot.value ?? undefined)
      : undefined)
))

watch(
  () => props.initialActivityType,
  value => {
    if (props.embedded && isActivityType(value)) embeddedType.value = value
  },
  { immediate: true },
)

watch(
  () => route.query.activity,
  async value => {
    if (props.embedded) return
    if (isActivityType(value)) return
    const latest = await getLatestFanxiuExchangeActivitySnapshot(
      activityOptions.map(item => item.value),
    )
    if (isActivityType(route.query.activity)) return
    const latestType = isActivityType(latest.activity_type)
      ? latest.activity_type
      : activityOptions[0].value
    resolvedDefaultType.value = latestType
    resolvedInitialSnapshot.value = latest.activity_type === latestType
      ? (latest.snapshot ?? null)
      : null
  },
  { immediate: true },
)
</script>

<template>
  <div class="top-activity-page" :class="{ 'is-embedded': embedded }">
    <header v-if="!embedded" class="page-header">
      <h2>玩法榜</h2>
    </header>

    <component
      :is="activePage"
      v-if="activePage"
      :key="selectedType"
      v-bind="activePageProps"
      embedded
      :initial-snapshot="selectedInitialSnapshot"
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

.top-activity-page.is-embedded {
  gap: 0;
  padding: 0;
}

.page-header h2 {
  margin: 0;
}

.activity-type-select {
  width: 150px;
}
</style>
