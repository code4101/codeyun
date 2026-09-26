<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import LinuxDoBookReaderDialog from '../library/LinuxDoBookReaderDialog.vue'
import SkillBookReaderDialog from '../library/SkillBookReaderDialog.vue'

const route = useRoute()
const bookId = computed(() => String(route.params.bookId || ''))
const bookshelfId = computed(() => typeof route.query.bookshelf === 'string' ? route.query.bookshelf : '')
const pageSize = computed(() => {
  const value = Number(route.query.pageSize)
  return Number.isInteger(value) && value >= 200 && value <= 20000 ? value : 1600
})
</script>

<template>
  <SkillBookReaderDialog v-if="bookId === 'local-skill'" :key="bookId" standalone :model-value="true" :bookshelf-id="bookshelfId" />
  <LinuxDoBookReaderDialog v-else :key="bookId" standalone :model-value="true" :book-id="bookId"
    :logical-page-target-characters="pageSize" :reading-mode="route.query.mode === 'paginated' ? 'paginated' : 'scroll'" />
</template>
