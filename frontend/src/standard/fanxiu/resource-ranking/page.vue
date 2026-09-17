<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getLatestFanxiuExchangeActivitySnapshot } from '@/api/fanxiu'
import { LINGCHONG_JINGWU_OFFICIAL_NAME } from '../lingchong-jingwu/model'
import { LIANTI_FAXIANG_OFFICIAL_NAME } from '../lianti-faxiang/model'
import { DANDAO_WENDING_OFFICIAL_NAME } from '../dandao-wending/model'
import PeakraceTotal from '../schedule/PeakraceTotal.vue'
import type { FanxiuExchangeActivitySnapshot } from '@/api/fanxiu'

const LingzhuangHuadaoPage = defineAsyncComponent(() => import('../lingzhuang-huadao/page.vue'))
const YaochiFlowerFestivalPage = defineAsyncComponent(() => import('../yaochi-flower-festival/page.vue'))
const YuandingSanshengPage = defineAsyncComponent(() => import('../yuanding-sansheng/page.vue'))
const LingchongJingwuPage = defineAsyncComponent(() => import('../lingchong-jingwu/page.vue'))
const ShequnLingchongPage = defineAsyncComponent(() => import('../shequn-lingchong/page.vue'))
const LiantiFaxiangPage = defineAsyncComponent(() => import('../lianti-faxiang/page.vue'))
const DandaoWendingPage = defineAsyncComponent(() => import('../dandao-wending/page.vue'))
const XilingZhengwuPage = defineAsyncComponent(() => import('../xiling-zhengwu/page.vue'))

type ResourceActivityType =
  | 'xiling-zhengwu'
  | 'lingzhuang-huadao'
  | 'yaochi-flower-festival'
  | 'yuanding-sansheng'
  | 'lingchong-jingwu'
  | 'shequn-lingchong'
  | 'lianti-faxiang'
  | 'dandao-wending'
  | 'peakrace'

const props = withDefaults(defineProps<{
  embedded?: boolean
  initialActivityType?: ResourceActivityType | null
  initialSnapshot?: FanxiuExchangeActivitySnapshot | null
}>(), {
  embedded: false,
  initialActivityType: null,
  initialSnapshot: null,
})

const activityOptions: { label: string; value: ResourceActivityType }[] = [
  { label: '洗灵证武', value: 'xiling-zhengwu' },
  { label: '灵装化道', value: 'lingzhuang-huadao' },
  { label: '瑶池花会', value: 'yaochi-flower-festival' },
  { label: '缘定三生', value: 'yuanding-sansheng' },
  { label: LINGCHONG_JINGWU_OFFICIAL_NAME, value: 'lingchong-jingwu' },
  { label: '社团灵宠', value: 'shequn-lingchong' },
  { label: LIANTI_FAXIANG_OFFICIAL_NAME, value: 'lianti-faxiang' },
  { label: DANDAO_WENDING_OFFICIAL_NAME, value: 'dandao-wending' },
  { label: '天道巅峰', value: 'peakrace' },
]
const route = useRoute()
const router = useRouter()
const resolvedDefaultType = ref<ResourceActivityType | null>(null)
const embeddedType = ref<ResourceActivityType | null>(null)

function isResourceActivityType(value: unknown): value is ResourceActivityType {
  return activityOptions.some(item => item.value === value)
}

const selectedType = computed<ResourceActivityType>({
  get: () => props.embedded
    ? (embeddedType.value ?? props.initialActivityType ?? activityOptions[0].value)
    : (isResourceActivityType(route.query.activity)
      ? route.query.activity
      : (resolvedDefaultType.value ?? activityOptions[0].value)),
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

watch(
  () => props.initialActivityType,
  value => {
    if (props.embedded && isResourceActivityType(value)) embeddedType.value = value
  },
  { immediate: true },
)

const selectedPage = computed(() => ({
  'xiling-zhengwu': XilingZhengwuPage,
  'lingzhuang-huadao': LingzhuangHuadaoPage,
  'yaochi-flower-festival': YaochiFlowerFestivalPage,
  'yuanding-sansheng': YuandingSanshengPage,
  'lingchong-jingwu': LingchongJingwuPage,
  'shequn-lingchong': ShequnLingchongPage,
  'lianti-faxiang': LiantiFaxiangPage,
  'dandao-wending': DandaoWendingPage,
  'peakrace': PeakraceTotal,
})[selectedType.value] ?? null)

watch(
  () => route.query.activity,
  async value => {
    if (props.embedded) return
    if (isResourceActivityType(value)) return
    const latest = await getLatestFanxiuExchangeActivitySnapshot(
      activityOptions.map(item => item.value),
    )
    if (isResourceActivityType(route.query.activity)) return
    resolvedDefaultType.value = isResourceActivityType(latest.activity_type)
      ? latest.activity_type
      : activityOptions[0].value
  },
  { immediate: true },
)
</script>

<template>
  <div class="resource-ranking-page" :class="{ 'is-embedded': embedded }">
    <header v-if="!embedded" class="page-header">
      <h2>资源榜</h2>
    </header>

    <component
      :is="selectedPage"
      v-if="selectedPage && (embedded || isResourceActivityType(route.query.activity) || resolvedDefaultType)"
      embedded
      v-bind="selectedType === 'peakrace' ? { snapshot: initialActivityType === 'peakrace' ? initialSnapshot : null } : {}"
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
.resource-ranking-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 20px;
}

.resource-ranking-page.is-embedded {
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
