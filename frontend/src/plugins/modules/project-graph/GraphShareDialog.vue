<script setup lang="ts">
import { onMounted, ref } from 'vue'
import AccountUserSelect from '@/components/AccountUserSelect.vue'
import type { AccountUserOption } from '@/api/accountUsers'
import type { GraphAccess, createGraphLibrary } from './storage'
const props = defineProps<{ documentId: string; title: string; library: ReturnType<typeof createGraphLibrary> }>()
const emit = defineEmits<{ close: [] }>()
const access = ref<GraphAccess>(), account = ref(''), selected = ref<AccountUserOption | null>(null)
const role = ref<'viewer' | 'editor'>('viewer'), busy = ref(false), error = ref(''), copied = ref(false)
async function run(action: () => Promise<void>) {
  busy.value = true; error.value = ''
  try { await action() } catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
  finally { busy.value = false }
}
async function update(userId: number, value: 'viewer' | 'editor' | 'deny') {
  await run(async () => { access.value = await props.library.setAccess(props.documentId, userId, value); selected.value = null; account.value = '' })
}
async function copyLink() {
  await run(async () => {
    const url = new URL(location.href); url.searchParams.set('doc', props.documentId)
    await navigator.clipboard.writeText(url.href); copied.value = true
  })
}
onMounted(() => run(async () => { access.value = await props.library.getAccess(props.documentId) }))
</script>
<template>
  <el-dialog :model-value="true" :title="`分享：${title}`" width="520px" @close="emit('close')">
    <p v-if="access">所有者：{{ access.owner.nickname || access.owner.username }}。仅受邀账号可以访问。</p>
    <div class="invite">
      <AccountUserSelect v-model="account" :disabled="busy" :exclude-usernames="access ? [access.owner.username] : []" @selected="selected = $event" />
      <el-select v-model="role" :disabled="busy" aria-label="邀请权限"><el-option label="只读" value="viewer" /><el-option label="可编辑" value="editor" /></el-select>
      <el-button :disabled="!selected || busy" @click="selected && update(selected.id, role)">添加</el-button>
    </div>
    <div v-for="grant in access?.grants.filter(item => item.role !== 'deny')" :key="grant.userId" class="grant">
      <span>{{ grant.nickname || grant.username }}</span>
      <el-select :model-value="grant.role" :disabled="busy" :aria-label="`${grant.username} 的权限`" @change="update(grant.userId, $event)">
        <el-option label="只读" value="viewer" /><el-option label="可编辑" value="editor" /><el-option label="移除访问权限" value="deny" />
      </el-select>
    </div>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <template #footer><el-button :disabled="busy" @click="copyLink">{{ copied ? '链接已复制' : '复制文件链接' }}</el-button><el-button @click="emit('close')">关闭</el-button></template>
  </el-dialog>
</template>
<style scoped>
.invite,.grant{display:flex;gap:10px;align-items:center;margin:14px 0}.invite :deep(.el-autocomplete){flex:1;min-width:0}.el-select{width:120px}.grant span{flex:1}.error{color:var(--el-color-danger)}
</style>
