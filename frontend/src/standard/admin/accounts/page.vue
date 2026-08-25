<template>
  <div class="account-manager">
    <div class="page-header">
      <div class="page-title-group">
        <h2 class="page-title">账号管理</h2>
        <p class="page-subtitle">
          查看系统账号，并在下方直接配置游客基线或当前选中账号的功能权限。
        </p>
      </div>
      <div class="header-actions">
        <el-button type="primary" plain @click="openCreateDialog">
          新增账号
        </el-button>
      </div>
    </div>

    <div class="content-stack">
      <div class="table-panel">
        <div class="panel-header">
          <div class="panel-header-main">
            <h3 class="panel-title">权限主体</h3>
            <p class="panel-subtitle">普通用户未单独配置时跟随游客基线，超管始终拥有全部功能权限。</p>
          </div>
          <el-button
            :type="selectedSubject.kind === 'anonymous' ? 'primary' : 'default'"
            plain
            @click="selectAnonymousSubject"
          >
            游客基线
          </el-button>
        </div>

        <el-table
          :data="accounts"
          row-key="id"
          v-loading="loading"
          empty-text="暂无账号"
          @row-click="selectUserSubject"
          :row-class-name="getAccountRowClassName"
        >
          <el-table-column label="账号" width="200">
            <template #default="{ row }">
              <div class="account-cell">
                <span class="account-name">{{ row.username }}</span>
                <el-tag
                  v-if="row.id === currentUserId"
                  size="small"
                  effect="plain"
                >
                  当前账号
                </el-tag>
                <el-tag
                  v-if="selectedSubject.kind === 'user' && selectedSubject.userId === row.id"
                  size="small"
                  type="primary"
                  effect="light"
                >
                  已选中
                </el-tag>
                <el-tag
                  v-if="!row.is_active"
                  type="warning"
                  size="small"
                  effect="light"
                >
                  已停用
                </el-tag>
              </div>
            </template>
          </el-table-column>

          <el-table-column label="昵称" width="140">
            <template #default="{ row }">
              <span :class="row.nickname ? 'nickname-text' : 'nickname-placeholder'">
                {{ row.nickname || '-' }}
              </span>
            </template>
          </el-table-column>

          <el-table-column label="备注" width="180" show-overflow-tooltip>
            <template #default="{ row }">
              <span :class="row.admin_note ? 'nickname-text' : 'nickname-placeholder'">
                {{ row.admin_note || '-' }}
              </span>
            </template>
          </el-table-column>

          <el-table-column label="权限类型" width="140">
            <template #default="{ row }">
              <el-tag
                :type="row.is_superuser ? 'danger' : 'info'"
                effect="light"
              >
                {{ row.is_superuser ? '超级管理员' : '普通账号' }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column label="创建时间" width="160">
            <template #default="{ row }">
              {{ formatCreatedAt(row.created_at) }}
            </template>
          </el-table-column>

          <el-table-column label="操作" width="108" align="center">
            <template #default="{ row }">
              <div class="action-cell">
                <el-button
                  text
                  type="primary"
                  size="small"
                  @click.stop="openProfileDialog(row)"
                >
                  编辑
                </el-button>
                <el-button
                  text
                  type="danger"
                  size="small"
                  @click.stop="confirmDeleteAccount(row)"
                  :disabled="row.id === currentUserId"
                >
                  删除
                </el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="permission-panel">
        <div class="panel-header">
          <div class="panel-header-main">
            <h3 class="panel-title">{{ selectedSubjectTitle }}</h3>
            <p class="panel-subtitle">{{ selectedSubjectSubtitle }}</p>
          </div>
          <div class="permission-header-actions">
            <el-tag
              size="small"
              effect="plain"
              :type="selectedSubject.kind === 'anonymous' ? 'info' : (selectedSubjectAccount?.is_superuser ? 'danger' : 'primary')"
            >
              {{ selectedSubjectTagLabel }}
            </el-tag>
            <el-button-group v-if="expandablePermissionKeys.length">
              <el-button
                size="small"
                :disabled="permissionTreeCollapsedKeys.size === 0"
                @click="expandAllPermissions"
              >
                全部展开
              </el-button>
              <el-button
                size="small"
                :disabled="collapsedExpandablePermissionCount === expandablePermissionKeys.length"
                @click="collapseAllPermissions"
              >
                全部收起
              </el-button>
            </el-button-group>
            <el-button
              size="small"
              @click="loadSelectedFeatureAccessContext"
              :loading="featureAccessLoading"
            >
              刷新权限
            </el-button>
          </div>
        </div>

        <div class="permission-hint">{{ permissionHintText }}</div>

        <div v-if="featureAccessLoading" class="permission-state">
          正在加载权限视图...
        </div>

        <div v-else-if="!featureAccessContext" class="permission-state">
          请选择要查看的权限主体。
        </div>

        <div v-else-if="isSelectedSubjectReadonly" class="permission-readonly">
          <el-alert
            type="info"
            :closable="false"
            title="超级管理员恒有全部功能权限"
            description="超管主体不需要单独配置功能树，这里仅做只读展示。"
          />
          <div class="permission-tree">
            <FeatureAccessTreeNode
              v-for="item in featureAccessContext.items"
              :key="item.key"
              :item="item"
              :depth="0"
              :subject-kind="selectedSubject.kind"
              :collapsed-keys="permissionTreeCollapsedKeys"
              disabled
              @change-decision="handleFeatureDecisionChange"
              @toggle-collapse="togglePermissionCollapse"
            />
          </div>
        </div>

        <div v-else class="permission-tree">
          <FeatureAccessTreeNode
            v-for="item in featureAccessContext.items"
            :key="item.key"
            :item="item"
            :depth="0"
            :subject-kind="selectedSubject.kind"
            :disabled="featureAccessSaving"
            :collapsed-keys="permissionTreeCollapsedKeys"
            @change-decision="handleFeatureDecisionChange"
            @toggle-collapse="togglePermissionCollapse"
          />
        </div>
      </div>
    </div>

    <el-dialog
      v-model="createDialogVisible"
      title="新增账号"
      width="480px"
      append-to-body
      destroy-on-close
      @closed="resetCreateForm"
    >
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="账号">
          <el-input
            v-model="createUsernameValue"
            placeholder="输入标准账号名"
            @keyup.enter="submitCreateAccount"
          />
        </el-form-item>
        <el-form-item label="密码">
          <div style="display: flex; gap: 8px; width: 100%;">
            <el-input
              v-model="createPasswordValue"
              type="text"
              placeholder="输入初始密码"
              @keyup.enter="submitCreateAccount"
              style="flex: 1;"
            />
            <el-button type="primary" plain @click="generateRandomPassword">
              生成随机密码
            </el-button>
          </div>
        </el-form-item>
        <el-form-item label="权限类型">
          <el-select v-model="createIsSuperuser" class="profile-select">
            <el-option :value="true" label="超级管理员" />
            <el-option :value="false" label="普通账号" />
          </el-select>
        </el-form-item>
        <el-form-item label="账号状态">
          <el-checkbox v-model="createIsActive">激活状态</el-checkbox>
        </el-form-item>
        <el-form-item label="昵称">
          <el-input
            v-model="createNicknameValue"
            placeholder="留空表示不填写"
            @keyup.enter="submitCreateAccount"
          />
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="createAdminNoteValue"
            placeholder="仅管理员可见，用于标记账号身份"
            @keyup.enter="submitCreateAccount"
          />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input
            v-model="createEmailValue"
            type="email"
            placeholder="留空表示不填写"
            @keyup.enter="submitCreateAccount"
          />
        </el-form-item>
        <el-form-item label="手机号">
          <el-input
            v-model="createPhoneValue"
            type="tel"
            placeholder="留空表示不填写"
            @keyup.enter="submitCreateAccount"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="createDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="creatingAccount" @click="submitCreateAccount">
          创建账号
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="profileDialogVisible"
      title="编辑资料"
      width="480px"
      append-to-body
      destroy-on-close
      @closed="resetProfileForm"
    >
      <el-form
        class="profile-form"
        label-position="right"
        label-width="72px"
        @submit.prevent
      >
        <el-form-item label="账号">
          <div class="dialog-account-name">{{ profileTarget?.username || '-' }}</div>
        </el-form-item>
        <el-form-item label="昵称">
          <el-input
            v-model="profileNicknameValue"
            placeholder="留空表示不填写"
            @keyup.enter="submitProfile"
          />
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="profileAdminNoteValue"
            placeholder="仅管理员可见，用于标记账号身份"
            @keyup.enter="submitProfile"
          />
        </el-form-item>
        <el-form-item label="权限类型">
          <el-select v-model="profileIsSuperuser" class="profile-select" :disabled="profileTarget?.id === currentUserId">
            <el-option :value="true" label="超级管理员" />
            <el-option :value="false" label="普通账号" />
          </el-select>
        </el-form-item>
        <el-form-item label="账号状态">
          <el-checkbox v-model="profileIsActive" :disabled="profileTarget?.id === currentUserId">激活状态</el-checkbox>
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input
            v-model="profileEmailValue"
            type="email"
            placeholder="留空表示不填写"
            @keyup.enter="submitProfile"
          />
        </el-form-item>
        <el-form-item label="手机号">
          <el-input
            v-model="profilePhoneValue"
            type="tel"
            placeholder="留空表示不填写"
            @keyup.enter="submitProfile"
          />
        </el-form-item>
        <el-form-item label="密码">
          <div class="password-field">
            <el-input
              :model-value="profilePasswordValue"
              :type="profilePasswordVisible ? 'text' : 'password'"
              readonly
            />
            <el-button @click="profilePasswordVisible = !profilePasswordVisible">
              {{ profilePasswordVisible ? '隐藏' : '显示' }}
            </el-button>
          </div>
        </el-form-item>
        <el-form-item label="新密码">
          <div class="profile-action-field">
            <el-input
              v-model="profileNewPasswordValue"
              type="text"
              placeholder="留空表示不修改"
              @keyup.enter="submitProfile"
            />
            <el-button type="primary" plain @click="generateProfileRandomPassword">
              生成随机密码
            </el-button>
          </div>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="profileDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingProfile" @click="submitProfile">
          保存资料
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { useRouter } from 'vue-router';

import FeatureAccessTreeNode from '@/components/admin/FeatureAccessTreeNode.vue';
import {
  type FeatureAccessContext,
  type FeatureAccessDecision,
  type FeatureAccessTreeItem,
} from '@/api/access';
import {
  fetchAdminAnonymousFeatureAccessContext,
  fetchAdminUserFeatureAccessContext,
  updateAdminAnonymousFeatureAccessContext,
  updateAdminUserFeatureAccessContext,
} from '@/api/adminFeatureAccess';
import {
  createAdminAccount,
  deleteAdminAccount,
  fetchAdminAccounts,
  updateAdminAccountProfile,
  type AdminAccountSummary,
} from '@/api/admin';
import { useFeatureAccessStore } from '@/store/featureAccessStore';
import { useUserStore } from '@/store/userStore';
import { formatNoteDateTime } from '@/utils/noteDate';

type FeatureAccessSubjectSelection =
  | { kind: 'anonymous' }
  | { kind: 'user'; userId: number };

const PERMISSION_TREE_COLLAPSED_STORAGE_KEY = 'admin.accounts.permissionTreeCollapsedKeys';

const loadPermissionTreeCollapsedKeys = () => {
  if (typeof window === 'undefined') {
    return new Set<string>();
  }
  try {
    const storedValue = JSON.parse(
      window.localStorage.getItem(PERMISSION_TREE_COLLAPSED_STORAGE_KEY) || '[]',
    );
    return new Set<string>(
      Array.isArray(storedValue)
        ? storedValue.filter((key): key is string => typeof key === 'string')
        : [],
    );
  } catch {
    return new Set<string>();
  }
};

const userStore = useUserStore();
const featureAccessStore = useFeatureAccessStore();
const router = useRouter();

const loading = ref(false);
const accounts = ref<AdminAccountSummary[]>([]);
const selectedSubject = ref<FeatureAccessSubjectSelection>({ kind: 'anonymous' });
const featureAccessContext = ref<FeatureAccessContext | null>(null);
const featureAccessLoading = ref(false);
const featureAccessSaving = ref(false);
const permissionTreeCollapsedKeys = ref<Set<string>>(loadPermissionTreeCollapsedKeys());

const createDialogVisible = ref(false);
const creatingAccount = ref(false);
const createUsernameValue = ref('');
const createPasswordValue = ref('');
const createNicknameValue = ref('');
const createAdminNoteValue = ref('');
const createIsSuperuser = ref(false);
const createIsActive = ref(true);
const createEmailValue = ref('');
const createPhoneValue = ref('');

const profileDialogVisible = ref(false);
const savingProfile = ref(false);
const profileTarget = ref<AdminAccountSummary | null>(null);
const profileNicknameValue = ref('');
const profileAdminNoteValue = ref('');
const profileIsSuperuser = ref(false);
const profileIsActive = ref(true);
const profileEmailValue = ref('');
const profilePhoneValue = ref('');
const profilePasswordVisible = ref(false);
const profileNewPasswordValue = ref('');

const currentUserId = computed(() => userStore.user?.id ?? null);
const profilePasswordValue = computed(() => profileTarget.value?.password_plain || '未知');

const selectedSubjectAccount = computed(() => {
  if (selectedSubject.value.kind !== 'user') {
    return null;
  }
  return accounts.value.find((account) => account.id === selectedSubject.value.userId) || null;
});

const selectedSubjectTitle = computed(() => {
  if (selectedSubject.value.kind === 'anonymous') {
    return '游客基线';
  }
  return selectedSubjectAccount.value?.username || '账号权限';
});

const selectedSubjectSubtitle = computed(() => {
  if (selectedSubject.value.kind === 'anonymous') {
    return '未登录访问时生效的保底权限；未单独配置的普通用户默认跟随它。';
  }
  if (selectedSubjectAccount.value?.is_superuser) {
    return '超级管理员恒有全部功能权限，当前为只读展示。';
  }
  return '当前账号未单独配置时跟随游客基线，可对目录树按节点做覆盖。';
});

const selectedSubjectTagLabel = computed(() => {
  if (selectedSubject.value.kind === 'anonymous') {
    return '游客基线';
  }
  return selectedSubjectAccount.value?.is_superuser ? '超级管理员' : '普通账号';
});

const isSelectedSubjectReadonly = computed(
  () => Boolean(selectedSubjectAccount.value?.is_superuser),
);

const collectExpandablePermissionKeys = (items: FeatureAccessTreeItem[]): string[] => (
  items.flatMap((item) => (
    item.children.length
      ? [item.key, ...collectExpandablePermissionKeys(item.children)]
      : []
  ))
);

const expandablePermissionKeys = computed(() => {
  return featureAccessContext.value
    ? collectExpandablePermissionKeys(featureAccessContext.value.items)
    : [];
});
const collapsedExpandablePermissionCount = computed(() => (
  expandablePermissionKeys.value.filter((key) => permissionTreeCollapsedKeys.value.has(key)).length
));

watch(permissionTreeCollapsedKeys, (collapsedKeys) => {
  if (typeof window !== 'undefined') {
    try {
      window.localStorage.setItem(
        PERMISSION_TREE_COLLAPSED_STORAGE_KEY,
        JSON.stringify([...collapsedKeys]),
      );
    } catch {
      // 浏览器禁用本地存储时，折叠功能仍在当前页面内正常工作。
    }
  }
});

const togglePermissionCollapse = (key: string) => {
  const nextCollapsedKeys = new Set(permissionTreeCollapsedKeys.value);
  if (nextCollapsedKeys.has(key)) {
    nextCollapsedKeys.delete(key);
  } else {
    nextCollapsedKeys.add(key);
  }
  permissionTreeCollapsedKeys.value = nextCollapsedKeys;
};

const expandAllPermissions = () => {
  permissionTreeCollapsedKeys.value = new Set<string>();
};

const collapseAllPermissions = () => {
  permissionTreeCollapsedKeys.value = new Set(expandablePermissionKeys.value);
};

const prunePermissionTreeCollapsedKeys = () => {
  const expandableKeys = new Set(expandablePermissionKeys.value);
  permissionTreeCollapsedKeys.value = new Set(
    [...permissionTreeCollapsedKeys.value].filter((key) => expandableKeys.has(key)),
  );
};
const permissionHintText = computed(() => {
  if (selectedSubject.value.kind === 'anonymous') {
    return '左侧勾选表示当前生效权限，右侧可切换“默认 / 开放 / 关闭”。关闭后游客默认不可见，未单独放开的普通用户也会一起关闭。';
  }
  return '左侧勾选表示当前生效权限，右侧可切换“继承游客 / 允许 / 禁止”。没有权限则菜单隐藏、路由不可进，对应后端功能也应直接拒绝。';
});

const formatCreatedAt = (createdAtSeconds: number) =>
  formatNoteDateTime(createdAtSeconds * 1000);

const replaceAccount = (nextAccount: AdminAccountSummary) => {
  accounts.value = accounts.value.map((account) =>
    account.id === nextAccount.id ? nextAccount : account,
  );
};

const resetCreateForm = () => {
  createUsernameValue.value = '';
  createPasswordValue.value = '';
  createNicknameValue.value = '';
  createAdminNoteValue.value = '';
  createIsSuperuser.value = false;
  createIsActive.value = true;
  createEmailValue.value = '';
  createPhoneValue.value = '';
};

const openCreateDialog = () => {
  resetCreateForm();
  createDialogVisible.value = true;
};

const syncCurrentUser = (nextAccount: AdminAccountSummary) => {
  if (!userStore.user || userStore.user.id !== nextAccount.id) {
    return;
  }

  userStore.user = {
    ...userStore.user,
    nickname: nextAccount.nickname,
    email: nextAccount.email || undefined,
    phone: nextAccount.phone,
    is_superuser: nextAccount.is_superuser,
  };
};

const resetProfileForm = () => {
  profileTarget.value = null;
  profileNicknameValue.value = '';
  profileAdminNoteValue.value = '';
  profileIsSuperuser.value = false;
  profileIsActive.value = true;
  profileEmailValue.value = '';
  profilePhoneValue.value = '';
  profilePasswordVisible.value = false;
  profileNewPasswordValue.value = '';
};

const openProfileDialog = (account: AdminAccountSummary) => {
  profileTarget.value = account;
  profileNicknameValue.value = account.nickname || '';
  profileAdminNoteValue.value = account.admin_note || '';
  profileIsSuperuser.value = account.is_superuser;
  profileIsActive.value = account.is_active;
  profileEmailValue.value = account.email || '';
  profilePhoneValue.value = account.phone || '';
  profilePasswordVisible.value = false;
  profileNewPasswordValue.value = '';
  profileDialogVisible.value = true;
};

const generateRandomPasswordStr = (): string => {
  let charset = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%';
  const excludeSet = new Set('iIl1oO0'.split(''));
  charset = charset.split('').filter(c => !excludeSet.has(c)).join('');

  const length = 16;
  let password = '';
  const array = new Uint32Array(length);
  window.crypto.getRandomValues(array);

  for (let j = 0; j < length; j++) {
    password += charset[array[j] % charset.length];
  }
  return password;
};

const generateRandomPassword = () => {
  createPasswordValue.value = generateRandomPasswordStr();
  ElMessage.success('已生成随机密码');
};

const generateProfileRandomPassword = () => {
  profileNewPasswordValue.value = generateRandomPasswordStr();
  ElMessage.success('已生成随机密码');
};

const loadSelectedFeatureAccessContext = async () => {
  featureAccessLoading.value = true;
  try {
    if (selectedSubject.value.kind === 'anonymous') {
      featureAccessContext.value = await fetchAdminAnonymousFeatureAccessContext();
    } else {
      featureAccessContext.value = await fetchAdminUserFeatureAccessContext(selectedSubject.value.userId);
    }
    prunePermissionTreeCollapsedKeys();
  } catch (error: any) {
    console.error(error);
    featureAccessContext.value = null;
    ElMessage.error(error.response?.data?.detail || '加载权限视图失败');
  } finally {
    featureAccessLoading.value = false;
  }
};

const selectAnonymousSubject = async () => {
  selectedSubject.value = { kind: 'anonymous' };
  await loadSelectedFeatureAccessContext();
};

const selectUserSubject = async (account: AdminAccountSummary) => {
  selectedSubject.value = {
    kind: 'user',
    userId: account.id,
  };
  await loadSelectedFeatureAccessContext();
};

const refreshRuntimeFeatureAccessIfNeeded = async () => {
  if (
    selectedSubject.value.kind === 'anonymous'
    || selectedSubject.value.userId === currentUserId.value
  ) {
    try {
      await featureAccessStore.refreshContext();
    } catch (error) {
      console.warn('Failed to refresh current runtime feature access:', error);
    }
  }
};

const handleFeatureDecisionChange = async (key: string, decision: FeatureAccessDecision) => {
  if (!featureAccessContext.value || featureAccessSaving.value || isSelectedSubjectReadonly.value) {
    return;
  }

  const nextOverrides = {
    ...featureAccessContext.value.overrides,
  };
  if (decision === 'inherit') {
    delete nextOverrides[key];
  } else {
    nextOverrides[key] = decision;
  }

  featureAccessSaving.value = true;
  try {
    if (selectedSubject.value.kind === 'anonymous') {
      featureAccessContext.value = await updateAdminAnonymousFeatureAccessContext(nextOverrides);
    } else {
      featureAccessContext.value = await updateAdminUserFeatureAccessContext(
        selectedSubject.value.userId,
        nextOverrides,
      );
    }
    await refreshRuntimeFeatureAccessIfNeeded();
  } catch (error: any) {
    console.error(error);
    ElMessage.error(error.response?.data?.detail || '更新权限失败');
  } finally {
    featureAccessSaving.value = false;
  }
};

const loadAccounts = async () => {
  loading.value = true;
  try {
    accounts.value = await fetchAdminAccounts();
    if (
      selectedSubject.value.kind === 'user'
      && !accounts.value.some((account) => account.id === selectedSubject.value.userId)
    ) {
      selectedSubject.value = { kind: 'anonymous' };
    }
    await loadSelectedFeatureAccessContext();
  } catch (error) {
    console.error(error);
    ElMessage.error('加载账号列表失败');
  } finally {
    loading.value = false;
  }
};

const submitCreateAccount = async () => {
  if (createUsernameValue.value.trim() === '') {
    ElMessage.warning('请输入账号');
    return;
  }
  if (createPasswordValue.value === '') {
    ElMessage.warning('请输入密码');
    return;
  }

  creatingAccount.value = true;
  try {
    const createdAccount = await createAdminAccount({
      username: createUsernameValue.value,
      password: createPasswordValue.value,
      nickname: createNicknameValue.value,
      adminNote: createAdminNoteValue.value,
      isSuperuser: createIsSuperuser.value,
      isActive: createIsActive.value,
      email: createEmailValue.value,
      phone: createPhoneValue.value,
    });
    createDialogVisible.value = false;
    await loadAccounts();
    ElMessage.success(`已创建账号 ${createdAccount.username}`);
  } catch (error: any) {
    console.error(error);
    ElMessage.error(error.response?.data?.detail || '创建账号失败');
  } finally {
    creatingAccount.value = false;
  }
};

const submitProfile = async () => {
  if (!profileTarget.value) return;
  const targetAccountId = profileTarget.value.id;

  savingProfile.value = true;
  try {
    const updatedAccount = await updateAdminAccountProfile(
      targetAccountId,
      profileNicknameValue.value,
      profileAdminNoteValue.value,
      profileIsSuperuser.value,
      profileIsActive.value,
      profileNewPasswordValue.value,
      profileEmailValue.value,
      profilePhoneValue.value,
    );
    replaceAccount(updatedAccount);
    syncCurrentUser(updatedAccount);
    profileDialogVisible.value = false;

    if (updatedAccount.id === currentUserId.value && !updatedAccount.is_superuser) {
      await featureAccessStore.refreshContext().catch(() => undefined);
      ElMessage.success('权限已更新，当前账号不再是超级管理员');
      await router.replace({ name: 'Home' });
      return;
    }

    await loadSelectedFeatureAccessContext();
    ElMessage.success(`已更新 ${updatedAccount.username} 的资料`);
  } catch (error: any) {
    console.error(error);
    ElMessage.error(error.response?.data?.detail || '保存资料失败');
  } finally {
    savingProfile.value = false;
  }
};

const confirmDeleteAccount = (account: AdminAccountSummary) => {
  ElMessageBox.confirm(
    `确定要删除账号 ${account.username} 吗？此操作不可恢复。`,
    '确认删除',
    {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    }
  ).then(async () => {
    try {
      await deleteAdminAccount(account.id);
      ElMessage.success(`已删除账号 ${account.username}`);
      await loadAccounts();
    } catch (error: any) {
      console.error(error);
      ElMessage.error(error.response?.data?.detail || '删除账号失败');
    }
  }).catch(() => {
    // cancelled
  });
};

const getAccountRowClassName = ({ row }: { row: AdminAccountSummary }) => {
  if (selectedSubject.value.kind === 'user' && selectedSubject.value.userId === row.id) {
    return 'is-selected-account-row';
  }
  return '';
};

onMounted(() => {
  loadAccounts();
});
</script>

<style scoped>
.account-manager {
  min-height: 100%;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px;
  box-sizing: border-box;
  background: #f5f7fa;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.header-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.page-title-group {
  min-width: 0;
}

.page-title {
  margin: 0;
  font-size: 24px;
  line-height: 1.2;
  color: #303133;
}

.page-subtitle {
  margin: 6px 0 0;
  font-size: 14px;
  color: #606266;
}

.content-stack {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.table-panel,
.permission-panel {
  min-height: 0;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  overflow: hidden;
}

.table-panel {
  display: flex;
  flex-direction: column;
  flex: 0 0 auto;
}

.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 16px;
  border-bottom: 1px solid #ebeef5;
}

.panel-header-main {
  min-width: 0;
}

.panel-title {
  margin: 0;
  font-size: 16px;
  color: #303133;
}

.panel-subtitle {
  margin: 6px 0 0;
  font-size: 13px;
  color: #909399;
}

.account-cell {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}

.account-name {
  min-width: 0;
  color: #303133;
  word-break: break-all;
}

.nickname-text,
.nickname-placeholder {
  display: inline-block;
  word-break: break-all;
}

.nickname-text {
  color: #303133;
}

.nickname-placeholder {
  color: #909399;
}

.action-cell {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-wrap: nowrap;
  gap: 4px;
  white-space: nowrap;
}

.action-cell :deep(.el-button) {
  margin-left: 0;
  flex: 0 0 auto;
}

.permission-panel {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
}

.permission-header-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.permission-hint {
  padding: 12px 16px;
  font-size: 13px;
  color: #606266;
  background: #fafafa;
  border-bottom: 1px solid #f0f2f5;
}

.permission-state,
.permission-readonly {
  padding: 16px;
}

.permission-tree {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 0 16px 16px;
}

.profile-form {
  padding-top: 2px;
}

.profile-form :deep(.el-form-item) {
  margin-bottom: 14px;
}

.profile-form :deep(.el-form-item:last-child) {
  margin-bottom: 0;
}

.profile-form :deep(.el-form-item__label) {
  min-height: 32px;
  line-height: 32px;
  color: #606266;
}

.profile-form :deep(.el-form-item__content) {
  min-width: 0;
  align-items: center;
}

.dialog-account-name {
  width: fit-content;
  max-width: 100%;
  min-width: 92px;
  min-height: 32px;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  padding: 0 11px;
  color: #303133;
  word-break: break-all;
  background: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}

.password-field {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  width: 100%;
}

.profile-action-field {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  width: 100%;
}

.profile-select {
  width: 100%;
}

.table-panel :deep(.is-selected-account-row) {
  --el-table-tr-bg-color: #eef5ff;
}

@media (max-width: 768px) {
  .page-header,
  .panel-header {
    flex-direction: column;
    align-items: stretch;
  }

  .header-actions,
  .permission-header-actions {
    justify-content: flex-end;
  }

}

@media (max-width: 520px) {
  .profile-form :deep(.el-form-item) {
    display: block;
  }

  .profile-form :deep(.el-form-item__label) {
    width: auto !important;
    min-height: 24px;
    line-height: 24px;
    padding: 0 0 6px;
    text-align: left;
  }
}
</style>
