<script setup lang="ts">
import { computed, ref } from 'vue'

import { fetchAccountUserOptions, type AccountUserOption } from '@/api/accountUsers'

const props = withDefaults(defineProps<{
  modelValue: string
  excludeUsernames?: string[]
  placeholder?: string
  disabled?: boolean
}>(), {
  excludeUsernames: () => [],
  placeholder: '搜索账号或昵称',
  disabled: false,
})

const emit = defineEmits<{
  (event: 'update:modelValue', value: string): void
  (event: 'selected', value: AccountUserOption | null): void
}>()

const loading = ref(false)
let requestId = 0

const excludedUsernameSet = computed(() => new Set(
  props.excludeUsernames.map((username) => username.trim()).filter(Boolean),
))
async function querySearch(
  query: string,
  callback: (options: AccountUserOption[]) => void,
) {
  const currentRequestId = ++requestId
  loading.value = true
  try {
    const response = await fetchAccountUserOptions(query)
    if (currentRequestId === requestId) {
      callback(response.users.filter(
        (option) => !excludedUsernameSet.value.has(option.username),
      ))
    }
  } catch (error) {
    console.warn('Failed to load account user options:', error)
    if (currentRequestId === requestId) {
      callback([])
    }
  } finally {
    if (currentRequestId === requestId) {
      loading.value = false
    }
  }
}

function updateValue(value: string) {
  emit('update:modelValue', value)
  emit('selected', null)
}

function selectValue(user: AccountUserOption) {
  emit('update:modelValue', user.username)
  emit('selected', user)
}
</script>

<template>
  <el-autocomplete
    :model-value="modelValue"
    clearable
    value-key="username"
    :fetch-suggestions="querySearch"
    :trigger-on-focus="true"
    :disabled="disabled"
    :loading="loading"
    :placeholder="placeholder"
    @update:model-value="updateValue"
    @select="selectValue"
  >
    <template #default="{ item: user }">
      <div class="account-user-option">
        <span>{{ user.username }}</span>
        <span v-if="user.nickname" class="account-user-option__nickname">{{ user.nickname }}</span>
      </div>
    </template>
  </el-autocomplete>
</template>

<style scoped>
.account-user-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.account-user-option__nickname {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
