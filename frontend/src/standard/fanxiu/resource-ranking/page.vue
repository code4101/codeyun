<script setup lang="ts">
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getLatestFanxiuExchangeActivitySnapshot } from '@/api/fanxiu'
import { LINGCHONG_JINGWU_OFFICIAL_NAME } from '../lingchong-jingwu/model'
import { LIANTI_FAXIANG_OFFICIAL_NAME } from '../lianti-faxiang/model'
import { DANDAO_WENDING_OFFICIAL_NAME } from '../dandao-wending/model'

const LingzhuangHuadaoPage = defineAsyncComponent(() => import('../lingzhuang-huadao/page.vue'))
const YaochiFlowerFestivalPage = defineAsyncComponent(() => import('../yaochi-flower-festival/page.vue'))
const YuandingSanshengPage = defineAsyncComponent(() => import('../yuanding-sansheng/page.vue'))
const LingchongJingwuPage = defineAsyncComponent(() => import('../lingchong-jingwu/page.vue'))
const LiantiFaxiangPage = defineAsyncComponent(() => import('../lianti-faxiang/page.vue'))
const DandaoWendingPage = defineAsyncComponent(() => import('../dandao-wending/page.vue'))

type ResourceActivityType =
  | 'lingzhuang-huadao'
  | 'yaochi-flower-festival'
  | 'yuanding-sansheng'
  | 'lingchong-jingwu'
  | 'lianti-faxiang'
  | 'dandao-wending'

const activityOptions: { label: string; value: ResourceActivityType }[] = [
  { label: '灵装化道', value: 'lingzhuang-huadao' },
  { label: '瑶池花会', value: 'yaochi-flower-festival' },
  { label: '缘定三生', value: 'yuanding-sansheng' },
  { label: LINGCHONG_JINGWU_OFFICIAL_NAME, value: 'lingchong-jingwu' },
  { label: LIANTI_FAXIANG_OFFICIAL_NAME, value: 'lianti-faxiang' },
  { label: DANDAO_WENDING_OFFICIAL_NAME, value: 'dandao-wending' },
]
const route = useRoute()
const router = useRouter()
const resolvedDefaultType = ref<ResourceActivityType | null>(null)

function isResourceActivityType(value: unknown): value is ResourceActivityType {
  return activityOptions.some(item => item.value === value)
}

const selectedType = computed<ResourceActivityType>({
  get: () => isResourceActivityType(route.query.activity)
    ? route.query.activity
    : (resolvedDefaultType.value ?? activityOptions[0].value),
  set(value) {
    void router.replace({
      query: {
        ...route.query,
        activity: value,
      },
    })
  },
})

const selectedPage = computed(() => ({
  'lingzhuang-huadao': LingzhuangHuadaoPage,
  'yaochi-flower-festival': YaochiFlowerFestivalPage,
  'yuanding-sansheng': YuandingSanshengPage,
  'lingchong-jingwu': LingchongJingwuPage,
  'lianti-faxiang': LiantiFaxiangPage,
  'dandao-wending': DandaoWendingPage,
})[selectedType.value] ?? null)

watch(
  () => route.query.activity,
  async value => {
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
  <div class="resource-ranking-page">
    <header class="page-header">
      <h2>资源榜</h2>
    </header>

    <component
      :is="selectedPage"
      v-if="selectedPage && (isResourceActivityType(route.query.activity) || resolvedDefaultType)"
      embedded
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

.page-header h2 {
  margin: 0;
}

.activity-type-select {
  width: 150px;
}

</style>
