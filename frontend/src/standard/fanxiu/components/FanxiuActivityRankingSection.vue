<script setup lang="ts">
import StandardPagination from '@/components/StandardPagination.vue'
import type { FanxiuExchangeRankingItem } from '@/api/fanxiu'
import { formatActivityUpdatedAt } from './activityStatus'
import FanxiuPlaneRankingTable from './FanxiuPlaneRankingTable.vue'
import FanxiuRankingKeyPointTable from './FanxiuRankingKeyPointTable.vue'

withDefaults(defineProps<{
  personalRows: FanxiuExchangeRankingItem[]
  planeRows?: FanxiuExchangeRankingItem[]
  showPlane?: boolean
  scoreLabel: string
  scorePerRewardLabel: string
  personalTotal?: number
  page?: number
  pageSize?: number
  personalLastCapturedAt?: string
  planeLastCapturedAt?: string
  loading?: boolean
  personalEmptyText?: string
  planeEmptyText?: string
  planeTitle?: string
  planeSubjectLabel?: string
}>(), {
  planeRows: () => [],
  showPlane: true,
  personalTotal: 0,
  page: 1,
  pageSize: 20,
  personalLastCapturedAt: '',
  planeLastCapturedAt: '',
  loading: false,
  personalEmptyText: '暂无个人榜数据',
  planeEmptyText: '暂无位面榜数据',
  planeTitle: '位面排名',
  planeSubjectLabel: '位面',
})

defineEmits<{
  pageChange: [page: number]
}>()
</script>

<template>
  <section class="fanxiu-ranking-section">
    <div class="section-heading">
      <h3>榜单情况</h3>
      <span v-if="personalLastCapturedAt" class="section-heading-note">
        {{ formatActivityUpdatedAt(personalLastCapturedAt) }}
      </span>
    </div>
    <div class="table-shell" v-loading="loading">
      <FanxiuRankingKeyPointTable
        :rows="personalRows"
        :score-label="scoreLabel"
        :score-per-reward-label="scorePerRewardLabel"
        :empty-text="personalEmptyText"
      />
      <StandardPagination
        v-if="personalTotal > pageSize"
        class="ranking-pagination"
        :page="page"
        :page-size="pageSize"
        :total="personalTotal"
        :show-page-size="false"
        @page-change="$emit('pageChange', $event)"
      />

      <template v-if="showPlane">
        <div class="subsection-heading">
          <h4>{{ planeTitle }}</h4>
          <span v-if="planeLastCapturedAt" class="section-heading-note">
            最后读取 {{ formatActivityUpdatedAt(planeLastCapturedAt) }}
          </span>
        </div>
        <FanxiuPlaneRankingTable
          :rows="planeRows"
          :score-label="scoreLabel"
          :empty-text="planeEmptyText"
          :subject-label="planeSubjectLabel"
        />
      </template>
    </div>
  </section>
</template>

<style scoped>
.fanxiu-ranking-section {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 10px;
}

.section-heading,
.subsection-heading {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.section-heading h3,
.subsection-heading h4 {
  margin: 0;
}

.section-heading-note {
  color: var(--el-text-color-secondary);
}

.table-shell {
  max-width: 100%;
  overflow-x: auto;
}

.ranking-pagination {
  margin-top: 10px;
}

.subsection-heading {
  margin-top: 18px;
  margin-bottom: 10px;
}
</style>
