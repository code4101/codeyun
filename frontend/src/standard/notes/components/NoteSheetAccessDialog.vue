<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import {
  fetchSheetAccess,
  fetchWorkbookAccess,
  updateSheetAccess,
  updateWorkbookAccess,
  type NoteSheetResourceAccess,
  type NoteSheetResourceAccessGrantItem,
  type NoteSheetResourceAccessGrantUpdate,
  type NoteSheetResourceRole,
  type NoteSheetResourceType,
} from '@/api/noteSheets'
import type { AccountUserOption } from '@/api/accountUsers'
import AccountUserSelect from '@/components/AccountUserSelect.vue'

type AccessAnonymousRole = 'none' | 'viewer'

type AccessUserGrantDraft = {
  key: string
  username: string
  nickname: string
  subjectUserId?: number | null
  role: Exclude<NoteSheetResourceRole, 'none'>
}

const props = defineProps<{
  modelValue: boolean
  resourceType: NoteSheetResourceType
  resourceId: number | null
  title?: string
}>()

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
  (event: 'saved', access: NoteSheetResourceAccess): void
}>()

const loading = ref(false)
const saving = ref(false)
const accessAnonymousRole = ref<AccessAnonymousRole>('none')
const accessUserGrants = ref<AccessUserGrantDraft[]>([])

const resourceLabel = computed(() => (props.resourceType === 'workbook' ? '工作簿' : '工作表'))
const dialogTitle = computed(() => `设置权限：${props.title || resourceLabel.value}`)

const userAccessRoleOptions = [
  { value: 'deny', label: '无权限' },
  { value: 'viewer', label: '只读' },
  { value: 'editor', label: '可编辑' },
  { value: 'manager', label: '可管理' },
] as const

function closeDialog() {
  emit('update:modelValue', false)
}

function handleDialogModelValueUpdate(value: boolean) {
  emit('update:modelValue', value)
}

function createAccessUserGrantDraft(): AccessUserGrantDraft {
  return {
    key: `${Date.now()}:${Math.random().toString(36).slice(2)}`,
    username: '',
    nickname: '',
    subjectUserId: null,
    role: 'viewer',
  }
}

function syncAccessUserGrantSelection(grant: AccessUserGrantDraft, user: AccountUserOption | null) {
  grant.username = user?.username ?? grant.username.trim()
  grant.nickname = user?.nickname ?? ''
  grant.subjectUserId = user?.id ?? null
}

function normalizeAccessDialogFromGrants(grants: NoteSheetResourceAccessGrantItem[]) {
  const anonymousGrant = grants.find((grant) => grant.subject_type === 'anonymous')
  accessAnonymousRole.value = anonymousGrant?.role === 'viewer' ? 'viewer' : 'none'
  accessUserGrants.value = grants
    .filter((grant) => grant.subject_type === 'user')
    .map((grant) => ({
      key: grant.subject_key,
      username: grant.username,
      nickname: grant.nickname,
      subjectUserId: grant.subject_user_id ?? null,
      role: grant.role,
    }))
}

async function fetchResourceAccess() {
  if (props.resourceId == null) {
    return null
  }
  return props.resourceType === 'workbook'
    ? fetchWorkbookAccess(props.resourceId)
    : fetchSheetAccess(props.resourceId)
}

async function updateResourceAccess(grants: NoteSheetResourceAccessGrantUpdate[]) {
  if (props.resourceId == null) {
    return null
  }
  return props.resourceType === 'workbook'
    ? updateWorkbookAccess(props.resourceId, grants)
    : updateSheetAccess(props.resourceId, grants)
}

async function loadAccessDialog() {
  if (!props.modelValue || props.resourceId == null) {
    return
  }

  loading.value = true
  accessUserGrants.value = []
  accessAnonymousRole.value = 'none'
  try {
    const detail = await fetchResourceAccess()
    if (!detail) {
      return
    }
    normalizeAccessDialogFromGrants(detail.grants)
  } catch (error) {
    console.warn('Failed to load note sheet resource access grants:', error)
    closeDialog()
    ElMessage.error('读取设置权限失败')
  } finally {
    loading.value = false
  }
}

function addAccessUserGrant() {
  accessUserGrants.value = [...accessUserGrants.value, createAccessUserGrantDraft()]
}

function removeAccessUserGrant(key: string) {
  accessUserGrants.value = accessUserGrants.value.filter((grant) => grant.key !== key)
}

function buildAccessGrantUpdates(): NoteSheetResourceAccessGrantUpdate[] {
  const grants: NoteSheetResourceAccessGrantUpdate[] = []
  if (accessAnonymousRole.value === 'viewer') {
    grants.push({
      subject_type: 'anonymous',
      role: 'viewer',
    })
  }

  const seenUsernames = new Set<string>()
  for (const grant of accessUserGrants.value) {
    const username = grant.username.trim()
    if (!username || seenUsernames.has(username)) {
      continue
    }
    seenUsernames.add(username)
    grants.push({
      subject_type: 'user',
      username,
      subject_user_id: grant.subjectUserId ?? undefined,
      role: grant.role,
    })
  }
  return grants
}

async function saveAccessDialog() {
  saving.value = true
  try {
    const detail = await updateResourceAccess(buildAccessGrantUpdates())
    if (!detail) {
      return
    }
    normalizeAccessDialogFromGrants(detail.grants)
    emit('saved', detail.access)
    ElMessage.success('权限设置已保存')
    closeDialog()
  } catch (error) {
    console.warn('Failed to save note sheet resource access grants:', error)
    ElMessage.error('保存权限设置失败，请检查用户名')
  } finally {
    saving.value = false
  }
}

watch(
  () => [props.modelValue, props.resourceType, props.resourceId] as const,
  () => {
    void loadAccessDialog()
  },
  { immediate: true },
)
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="dialogTitle"
    width="520px"
    class="resource-access-dialog"
    @update:model-value="handleDialogModelValueUpdate"
  >
    <div v-loading="loading" class="resource-access-body">
      <div class="resource-access-row">
        <label class="resource-access-label">游客</label>
        <el-select v-model="accessAnonymousRole" class="resource-access-control">
          <el-option value="none" label="无权限" />
          <el-option value="viewer" label="只读" />
        </el-select>
      </div>

      <div class="resource-access-users-header">
        <span>指定用户</span>
        <el-button size="small" @click="addAccessUserGrant">添加</el-button>
      </div>

      <div v-if="accessUserGrants.length" class="resource-access-users">
        <div v-for="grant in accessUserGrants" :key="grant.key" class="resource-access-user-row">
          <AccountUserSelect
            v-model="grant.username"
            class="resource-access-username"
            placeholder="选择用户"
            :exclude-usernames="accessUserGrants.filter(item => item.key !== grant.key).map(item => item.username)"
            @selected="user => syncAccessUserGrantSelection(grant, user)"
          />
          <el-select v-model="grant.role" class="resource-access-role">
            <el-option
              v-for="option in userAccessRoleOptions"
              :key="option.value"
              :value="option.value"
              :label="option.label"
            />
          </el-select>
          <button
            type="button"
            class="resource-access-remove"
            title="移除"
            aria-label="移除"
            @click="removeAccessUserGrant(grant.key)"
          >
            -
          </button>
        </div>
      </div>
      <div v-else class="resource-access-empty">未指定用户</div>
    </div>

    <template #footer>
      <el-button @click="closeDialog">取消</el-button>
      <el-button type="primary" :loading="saving" @click="saveAccessDialog">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.resource-access-body {
  min-height: 180px;
}

.resource-access-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.resource-access-label {
  flex: 0 0 72px;
  color: #5f6b7a;
  font-size: 14px;
  font-weight: 600;
}

.resource-access-control {
  flex: 1;
}

.resource-access-users-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid #edf1f5;
  color: #2f3a4a;
  font-size: 14px;
  font-weight: 600;
}

.resource-access-users {
  display: grid;
  gap: 10px;
  margin-top: 12px;
}

.resource-access-user-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 128px 28px;
  align-items: center;
  gap: 8px;
}

.resource-access-username,
.resource-access-role {
  width: 100%;
}

.resource-access-user-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}

.resource-access-user-option-name {
  min-width: 0;
  overflow: hidden;
  color: #1f2937;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.resource-access-user-option-nickname {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  color: #7a8594;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.resource-access-remove {
  width: 28px;
  height: 28px;
  border: 1px solid #f0c4c4;
  border-radius: 6px;
  background: #fff;
  color: #c45656;
  font-size: 18px;
  font-weight: 700;
  line-height: 1;
  cursor: pointer;
}

.resource-access-remove:hover {
  background: #fef2f2;
}

.resource-access-empty {
  margin-top: 12px;
  color: #8a95a5;
  font-size: 13px;
}
</style>
