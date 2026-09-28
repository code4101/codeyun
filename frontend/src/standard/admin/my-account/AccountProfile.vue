<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { isAxiosError } from 'axios'
import { ElDialog, ElMessage } from 'element-plus'
import { useUserStore } from '@/store/userStore'

const userStore = useUserStore()
const profile = reactive({ nickname: '', phone: '', email: '' })
const password = reactive({ current: '', next: '', confirm: '' })
const savingProfile = ref(false)
const savingPassword = ref(false)
const profileMessage = ref('')
const passwordMessage = ref('')
const profileFailed = ref(false)
const passwordDialogOpen = ref(false)

function clearPassword() {
  Object.assign(password, { current: '', next: '', confirm: '' })
  passwordMessage.value = ''
}
watch(passwordDialogOpen, open => { if (!open) clearPassword() })

watch(() => userStore.user, user => {
  Object.assign(profile, { nickname: user?.nickname ?? '', phone: user?.phone ?? '', email: user?.email ?? '' })
}, { immediate: true })
watch(() => userStore.user?.id, () => {
  passwordDialogOpen.value = false
  clearPassword()
  profileMessage.value = passwordMessage.value = ''
})

function errorMessage(error: unknown, fallback: string) {
  if (!isAxiosError(error)) return fallback
  const detail = error.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(item => item.msg).join('；')
  return fallback
}
async function saveProfile() {
  if (savingProfile.value) return
  savingProfile.value = true
  profileMessage.value = ''
  profileFailed.value = false
  try {
    await userStore.updateMyProfile({ ...profile })
    profileMessage.value = '个人资料已保存'
  } catch (error) {
    profileFailed.value = true
    profileMessage.value = errorMessage(error, '保存失败，请稍后重试')
  } finally { savingProfile.value = false }
}
async function savePassword() {
  if (savingPassword.value) return
  passwordMessage.value = ''
  if (password.next !== password.confirm) { passwordMessage.value = '两次输入的新密码不一致'; return }
  if (new TextEncoder().encode(password.next).length > 72) { passwordMessage.value = '密码不能超过 72 字节'; return }
  savingPassword.value = true
  try {
    await userStore.changeMyPassword(password.current, password.next)
    passwordDialogOpen.value = false
    clearPassword()
    ElMessage.success('密码已修改，下次登录请使用新密码')
  } catch (error) {
    passwordMessage.value = errorMessage(error, '修改失败，请稍后重试')
  } finally { savingPassword.value = false }
}
</script>

<template>
  <section class="account-profile" aria-label="账号设置">
    <form @submit.prevent="saveProfile">
      <h2>个人资料</h2>
      <fieldset :disabled="savingProfile || !userStore.user" class="profile-fields">
        <label>账号<input :value="userStore.user?.username ?? ''" name="username" autocomplete="username" readonly title="账号不可修改" /></label>
        <label>昵称<input v-model="profile.nickname" name="nickname" autocomplete="nickname" maxlength="80" placeholder="填写昵称" /></label>
        <label>手机<input v-model="profile.phone" name="phone" type="tel" autocomplete="tel" maxlength="40" placeholder="填写手机号" /></label>
        <label>邮箱<input v-model="profile.email" name="email" type="email" autocomplete="email" maxlength="254" placeholder="填写邮箱" /></label>
        <div class="profile-actions">
          <button type="submit">{{ savingProfile ? '更新中…' : '更新资料' }}</button>
          <button type="button" class="password-tool" @click="passwordDialogOpen = true">修改密码</button>
        </div>
      </fieldset>
      <p v-if="profileMessage" role="status" :class="{ failed: profileFailed }">{{ profileMessage }}</p>
    </form>
    <ElDialog
      v-model="passwordDialogOpen"
      title="修改密码"
      width="min(440px, calc(100vw - 32px))"
      align-center
      append-to-body
      destroy-on-close
      :close-on-click-modal="false"
      :close-on-press-escape="!savingPassword"
      :show-close="!savingPassword"
    >
      <form @submit.prevent="savePassword">
        <fieldset :disabled="savingPassword || !userStore.user" class="profile-fields">
          <label>当前密码<input v-model="password.current" name="current-password" type="password" autocomplete="current-password" required maxlength="1024" /></label>
          <label>新密码<input v-model="password.next" name="new-password" type="password" autocomplete="new-password" required minlength="8" maxlength="72" placeholder="至少 8 个字符" /></label>
          <label>确认新密码<input v-model="password.confirm" name="confirm-password" type="password" autocomplete="new-password" required minlength="8" maxlength="72" /></label>
          <div class="dialog-actions">
            <button type="button" class="cancel-button" @click="passwordDialogOpen = false">取消</button>
            <button type="submit">{{ savingPassword ? '保存中…' : '保存' }}</button>
          </div>
        </fieldset>
        <p v-if="passwordMessage" role="alert" class="failed">{{ passwordMessage }}</p>
      </form>
    </ElDialog>
  </section>
</template>

<style scoped>
.account-profile { margin-bottom: 24px; padding-bottom: 18px; border-bottom: 1px solid #e4e7ed; }
h2 { font-size: 16px; margin: 0 0 12px; }
.profile-fields { display: flex; flex-direction: column; align-items: stretch; gap: 12px; width: 100%; max-width: 520px; min-width: 0; box-sizing: border-box; border: 0; padding: 0; margin: 0; }
label { display: grid; grid-template-columns: 84px minmax(0, 1fr); align-items: center; gap: 12px; font-size: 13px; color: #606266; }
.profile-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-left: 96px; margin-top: 2px; }
input { box-sizing: border-box; width: 100%; min-width: 0; height: 34px; padding: 6px 10px; border: 1px solid #dcdfe6; border-radius: 4px; font: inherit; color: #303133; background: #fff; }
input:focus { outline: 2px solid var(--el-color-primary); outline-offset: -1px; }
input[readonly] { background: #f5f7fa; color: #909399; }
button { height: 34px; padding: 0 14px; border: 1px solid var(--el-color-primary); border-radius: 4px; background: var(--el-color-primary); color: #fff; font: inherit; font-size: 13px; cursor: pointer; }
fieldset:disabled { opacity: .65; }
.password-tool { background: transparent; color: #606266; border-color: #dcdfe6; }
.dialog-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }
.cancel-button { background: #fff; color: #606266; border-color: #dcdfe6; }
p { font-size: 12px; color: #389660; margin: 8px 0 0; }
p.failed { color: #d64343; }
</style>
