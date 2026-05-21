<template>
  <el-dialog
    v-model="dialogVisible"
    :title="accessDialogTitle"
    width="520px"
    append-to-body
    class="note-doc-access-dialog"
  >
    <div v-loading="accessDialogLoading" class="resource-access-body">
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
          <el-input
            v-model="grant.username"
            class="resource-access-username"
            placeholder="username"
            :title="grant.nickname || grant.username"
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
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="accessDialogSaving" @click="saveAccessDialog">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import {
  fetchNoteDocAccess,
  type NoteDocResourceAccessGrantItem,
  type NoteDocResourceAccessGrantUpdate,
  type NoteDocResourceAccess,
  type NoteDocResourceRole,
  type NoteNode,
  noteKey,
  updateNoteDocAccess,
} from '@/api/notes';

type AccessAnonymousRole = 'none' | 'viewer';

type AccessUserGrantDraft = {
  key: string;
  username: string;
  nickname: string;
  subjectUserId?: number | null;
  role: Exclude<NoteDocResourceRole, 'none'>;
};

const props = defineProps<{
  modelValue: boolean;
  noteRef?: string | null;
  title?: string | null;
  note?: NoteNode | null;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void;
  (e: 'update:access', value: NoteDocResourceAccess): void;
}>();

const accessDialogLoading = ref(false);
const accessDialogSaving = ref(false);
const accessAnonymousRole = ref<AccessAnonymousRole>('none');
const accessUserGrants = ref<AccessUserGrantDraft[]>([]);

const dialogVisible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
});

const accessDialogTitle = computed(() => `设置权限：${props.title || props.note?.title || '文档'}`);

const userAccessRoleOptions = [
  { value: 'deny', label: '无权限' },
  { value: 'viewer', label: '只读' },
  { value: 'editor', label: '可编辑' },
  { value: 'manager', label: '可管理' },
] as const;

const getDocRouteRef = (note: Pick<NoteNode, 'id' | 'numeric_id'>) => (
  note.numeric_id && note.numeric_id > 0 ? String(note.numeric_id) : noteKey(note.id)
);

const resolvedNoteRef = computed(() => {
  const explicitRef = String(props.noteRef || '').trim();
  if (explicitRef) return explicitRef;
  return props.note ? getDocRouteRef(props.note) : '';
});

function createAccessUserGrantDraft(): AccessUserGrantDraft {
  return {
    key: `${Date.now()}:${Math.random().toString(36).slice(2)}`,
    username: '',
    nickname: '',
    subjectUserId: null,
    role: 'viewer',
  };
}

function normalizeAccessDialogFromGrants(grants: NoteDocResourceAccessGrantItem[]) {
  const anonymousGrant = grants.find((grant) => grant.subject_type === 'anonymous');
  accessAnonymousRole.value = anonymousGrant?.role === 'viewer' ? 'viewer' : 'none';
  accessUserGrants.value = grants
    .filter((grant) => grant.subject_type === 'user')
    .map((grant) => ({
      key: grant.subject_key,
      username: grant.username,
      nickname: grant.nickname,
      subjectUserId: grant.subject_user_id ?? null,
      role: grant.role,
    }));
}

async function loadAccessDialog() {
  const noteRef = resolvedNoteRef.value;
  if (!noteRef) {
    dialogVisible.value = false;
    return;
  }

  accessDialogLoading.value = true;
  accessAnonymousRole.value = 'none';
  accessUserGrants.value = [];
  try {
    const detail = await fetchNoteDocAccess(noteRef);
    normalizeAccessDialogFromGrants(detail.grants);
  } catch (error) {
    console.warn('Failed to load doc access grants:', error);
    dialogVisible.value = false;
    ElMessage.error('读取设置权限失败');
  } finally {
    accessDialogLoading.value = false;
  }
}

function addAccessUserGrant() {
  accessUserGrants.value = [...accessUserGrants.value, createAccessUserGrantDraft()];
}

function removeAccessUserGrant(key: string) {
  accessUserGrants.value = accessUserGrants.value.filter((grant) => grant.key !== key);
}

function buildAccessGrantUpdates(): NoteDocResourceAccessGrantUpdate[] {
  const grants: NoteDocResourceAccessGrantUpdate[] = [];
  if (accessAnonymousRole.value === 'viewer') {
    grants.push({
      subject_type: 'anonymous',
      role: 'viewer',
    });
  }

  const seenUsernames = new Set<string>();
  for (const grant of accessUserGrants.value) {
    const username = grant.username.trim();
    if (!username || seenUsernames.has(username)) {
      continue;
    }
    seenUsernames.add(username);
    grants.push({
      subject_type: 'user',
      username,
      role: grant.role,
    });
  }
  return grants;
}

async function saveAccessDialog() {
  const noteRef = resolvedNoteRef.value;
  if (!noteRef) return;

  accessDialogSaving.value = true;
  try {
    const detail = await updateNoteDocAccess(noteRef, buildAccessGrantUpdates());
    normalizeAccessDialogFromGrants(detail.grants);
    emit('update:access', detail.access);
    ElMessage.success('权限设置已保存');
    dialogVisible.value = false;
  } catch (error) {
    console.warn('Failed to save doc access grants:', error);
    ElMessage.error('保存权限设置失败，请检查用户名');
  } finally {
    accessDialogSaving.value = false;
  }
}

watch(() => props.modelValue, (visible) => {
  if (visible) {
    void loadAccessDialog();
  }
});
</script>

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
