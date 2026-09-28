<script setup lang="ts">
import { onBeforeUnmount, reactive, ref, watch } from 'vue'
import { isAxiosError } from 'axios'
import { ElDialog, ElMessage } from 'element-plus'
import { Upload } from '@element-plus/icons-vue'
import { useUserStore } from '@/store/userStore'
import UserAvatar from '@/components/UserAvatar.vue'

const userStore = useUserStore()
const avatarInput = ref<HTMLInputElement | null>(null)
const savingAvatar = ref(false)
const draggingAvatar = ref(false)
async function updateAvatar(file: File) {
  if (savingAvatar.value || savingProfile.value || !userStore.user) return
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) && (file.type || !/\.(jpe?g|png|webp)$/i.test(file.name))) {
    ElMessage.error('请选择 JPG、PNG 或 WebP 图片'); return
  }
  if (file.size > 5 * 1024 * 1024) { ElMessage.error('头像不能超过 5 MB'); return }
  savingAvatar.value = true
  try {
    await userStore.updateMyAvatar(file)
    ElMessage.success('头像已更新')
  } catch (error) {
    ElMessage.error(errorMessage(error, '头像更新失败，请稍后重试'))
  } finally { savingAvatar.value = false }
}
function selectAvatar(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (file) void updateAvatar(file)
}
function dragAvatar(event: DragEvent) {
  if (!event.dataTransfer) return
  const allowed = !savingAvatar.value && !savingProfile.value && !!userStore.user && Array.from(event.dataTransfer.types).includes('Files')
  draggingAvatar.value = allowed
  event.dataTransfer.dropEffect = allowed ? 'copy' : 'none'
}
function dropAvatar(event: DragEvent) {
  draggingAvatar.value = false
  const files = event.dataTransfer?.files
  if (!files?.length) return
  if (files.length !== 1) { ElMessage.error('每次请选择一张头像图片'); return }
  void updateAvatar(files[0]!)
}
const profile = reactive({ username: '', nickname: '', phone: '', email: '' })
const password = reactive({ next: '', confirm: '' })
const generatingPassword = ref(false)
const showPassword = ref(false)
let generationVersion = 0
async function generatePassword() {
  if (generatingPassword.value) return
  const version = ++generationVersion
  generatingPassword.value = true
  passwordMessage.value = ''
  try {
    const generated = await userStore.generateMyPassword()
    if (version !== generationVersion) return
    password.next = generated
    password.confirm = ''
    showPassword.value = true
  } catch (error) {
    if (version === generationVersion) passwordMessage.value = errorMessage(error, '生成失败，请稍后重试')
  } finally { if (version === generationVersion) generatingPassword.value = false }
}
async function copyPassword() {
  try {
    await navigator.clipboard.writeText(password.next)
    ElMessage.success('已复制，请妥善保存')
  } catch { ElMessage.info('无法自动复制，请显示密码后手动复制') }
}
const strength = ref<{ score: number; label: string; accepted: boolean; reasons: string[] } | null>(null)
const assessing = ref(false)
const assessmentError = ref('')
let assessmentTimer: ReturnType<typeof setTimeout> | undefined
let assessmentRequest: AbortController | undefined
watch(() => [password.next, userStore.user?.username], () => {
  clearTimeout(assessmentTimer)
  assessmentRequest?.abort()
  strength.value = null
  assessmentError.value = ''
  assessing.value = !!password.next
  if (!password.next) return
  const current = new AbortController()
  assessmentRequest = current
  assessmentTimer = setTimeout(async () => {
    try {
      const result = await userStore.assessMyPassword(password.next, current.signal)
      if (!current.signal.aborted) strength.value = result
    } catch {
      if (!current.signal.aborted) assessmentError.value = '安全度评估暂不可用，请重新输入后重试'
    } finally { if (!current.signal.aborted) assessing.value = false }
  }, 250)
})
onBeforeUnmount(() => { clearTimeout(assessmentTimer); assessmentRequest?.abort() })
const savingProfile = ref(false)
const savingPassword = ref(false)
const profileMessage = ref('')
const passwordMessage = ref('')
const profileFailed = ref(false)
const passwordDialogOpen = ref(false)

function clearPassword() {
  generationVersion++
  generatingPassword.value = false
  showPassword.value = false
  Object.assign(password, { next: '', confirm: '' })
  passwordMessage.value = ''
}
watch(passwordDialogOpen, open => { if (!open) clearPassword() })

watch(() => userStore.user, user => {
  Object.assign(profile, { username: user?.username ?? '', nickname: user?.nickname ?? '', phone: user?.phone ?? '', email: user?.email ?? '' })
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
  if (assessing.value || !strength.value?.accepted) { passwordMessage.value = '请先设置符合安全要求的新密码'; return }
  if (new TextEncoder().encode(password.next).length > 72) { passwordMessage.value = '密码不能超过 72 字节'; return }
  savingPassword.value = true
  try {
    await userStore.changeMyPassword(password.next, password.confirm)
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
      <div v-if="userStore.user?.password_needs_reset" class="password-warning" role="alert">
        <span>当前密码过于简单，建议尽快重置密码。</span>
        <button type="button" class="password-tool" @click="passwordDialogOpen = true">重置密码</button>
      </div>
      <fieldset :disabled="savingProfile || !userStore.user" class="profile-fields">
        <div class="avatar-row">
          <span>头像</span>
          <div class="avatar-controls">
            <button
              type="button" class="avatar-dropzone" :class="{ dragging: draggingAvatar }"
              :disabled="savingAvatar" :aria-busy="savingAvatar" aria-label="点击或拖拽图片更新头像"
              @click="avatarInput?.click()" @dragenter.prevent="dragAvatar" @dragover.prevent="dragAvatar"
              @dragleave.prevent="draggingAvatar = false" @drop.prevent.stop="dropAvatar"
            >
              <UserAvatar :user="userStore.user" :size="80" />
              <span v-if="savingAvatar || draggingAvatar" class="avatar-overlay">{{ savingAvatar ? '上传中…' : '松开上传' }}</span>
            </button>
            <div>
              <div class="avatar-actions">
                <button type="button" class="password-tool avatar-upload" :disabled="savingAvatar" @click="avatarInput?.click()"><Upload aria-hidden="true" />{{ savingAvatar ? '更新中…' : '更新头像' }}</button>
              </div>
              <small>可以拖动图片到左边头像区域完成上传</small>
            </div>
            <input ref="avatarInput" type="file" accept="image/jpeg,image/png,image/webp" hidden aria-label="选择头像图片" :disabled="savingAvatar" @change="selectAvatar" />
          </div>
        </div>
        <label>账号<input v-model="profile.username" name="username" autocomplete="username" required maxlength="80" pattern="\S+" /></label>
        <label>昵称<input v-model="profile.nickname" name="nickname" autocomplete="nickname" maxlength="80" placeholder="填写昵称" /></label>
        <label>手机<input v-model="profile.phone" name="phone" type="tel" autocomplete="tel" maxlength="40" placeholder="填写手机号" /></label>
        <label>邮箱<input v-model="profile.email" name="email" type="email" autocomplete="email" maxlength="254" placeholder="填写邮箱" /></label>
        <div class="account-form-actions">
          <button type="submit">{{ savingProfile ? '更新中…' : '更新信息' }}</button>
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
        <fieldset :disabled="savingPassword || generatingPassword || !userStore.user" class="profile-fields">
          <label>新密码<input v-model="password.next" name="new-password" :type="showPassword ? 'text' : 'password'" autocomplete="new-password" required minlength="10" maxlength="72" placeholder="至少 10 个字符" /></label>
          <div class="password-tools">
            <button type="button" class="password-tool" @click="generatePassword">{{ generatingPassword ? '生成中…' : '生成随机密码' }}</button>
            <button type="button" class="password-tool" :aria-pressed="showPassword" @click="showPassword = !showPassword">{{ showPassword ? '隐藏' : '显示' }}</button>
            <button type="button" class="password-tool" :disabled="!password.next" @click="copyPassword">复制</button>
            <small>生成后请保存密码，并在下方再次输入确认。</small>
          </div>
          <div class="password-strength" aria-live="polite">
            <span v-if="assessing">正在评估安全度…</span>
            <template v-else-if="strength">
              <div class="strength-heading">安全度：{{ strength.label }}<small>规则评估，仅供参考</small></div>
              <meter min="0" max="4" low="2" high="3" optimum="4" :value="strength.score" aria-label="密码安全度" />
              <ul v-if="strength.reasons.length"><li v-for="reason in strength.reasons" :key="reason">{{ reason }}</li></ul>
            </template>
            <span v-else-if="assessmentError">{{ assessmentError }}</span>
            <span v-else>组合大小写字母、数字、符号中的三类，或使用较长的多词口令。</span>
          </div>
          <label>确认新密码<input v-model="password.confirm" name="confirm-password" type="password" autocomplete="new-password" required minlength="10" maxlength="72" /></label>
          <div class="dialog-actions">
            <button type="button" class="cancel-button" @click="passwordDialogOpen = false">取消</button>
            <button type="submit" :disabled="assessing || !strength?.accepted">{{ savingPassword ? '保存中…' : '保存' }}</button>
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
.avatar-row { display: flex; flex-direction: column; gap: 10px; margin-bottom: 8px; font-size: 13px; color: #606266; }
.avatar-controls { display: flex; align-items: center; gap: 16px; min-width: 0; }
.avatar-controls > .avatar-dropzone { position: relative; display: inline-flex; flex: 0 0 80px; width: 80px; height: 80px; padding: 0; border: 0; border-radius: 50%; background: transparent; }
.avatar-dropzone :deep(.user-avatar) { border-radius: 50%; pointer-events: none; }
.avatar-dropzone:hover, .avatar-dropzone:focus-visible, .avatar-dropzone.dragging { outline: 2px solid var(--el-color-primary); outline-offset: 3px; }
.avatar-overlay { position: absolute; inset: 0; display: grid; place-items: center; border-radius: 50%; background: #0008; color: #fff; font-size: 12px; pointer-events: none; }
.avatar-actions .avatar-upload { display: inline-flex; align-items: center; gap: 5px; height: 30px; padding: 0 10px; }
.avatar-upload svg { width: 16px; height: 16px; }
.avatar-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 6px; }
.avatar-controls small { font-size: 12px; color: #909399; }
.account-form-actions { display: inline-flex; align-self: flex-start; flex-wrap: wrap; gap: 10px; margin-left: 96px; margin-top: 2px; }
.account-form-actions > button { display: inline-flex; align-items: center; justify-content: center; width: max-content; min-width: 0; flex: 0 0 auto; white-space: nowrap; }
input { box-sizing: border-box; width: 100%; min-width: 0; height: 34px; padding: 6px 10px; border: 1px solid #dcdfe6; border-radius: 4px; font: inherit; color: #303133; background: #fff; }
input:focus { outline: 2px solid var(--el-color-primary); outline-offset: -1px; }
input[readonly] { background: #f5f7fa; color: #909399; }
button { height: 34px; padding: 0 14px; border: 1px solid var(--el-color-primary); border-radius: 4px; background: var(--el-color-primary); color: #fff; font: inherit; font-size: 13px; cursor: pointer; }
button:disabled { opacity: .5; cursor: not-allowed; }
.password-strength { margin-left: 96px; font-size: 12px; color: #737984; line-height: 1.6; }
.password-warning { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; max-width: 520px; box-sizing: border-box; margin-bottom: 16px; padding: 10px 12px; border: 1px solid #f3d19e; border-radius: 4px; background: #fdf6ec; color: #976118; font-size: 13px; }
.password-warning button { height: 28px; padding: 0 10px; }
.password-tools { display: flex; flex-wrap: wrap; gap: 8px; margin-left: 96px; }
.password-tools button { height: 28px; padding: 0 8px; }
.password-tools small { flex-basis: 100%; color: #909399; font-size: 12px; }
.strength-heading { display: flex; justify-content: space-between; gap: 8px; }
.password-strength meter { width: 100%; height: 12px; }
.password-strength ul { margin: 4px 0 0; padding-left: 16px; }
fieldset:disabled { opacity: .65; }
.password-tool { background: transparent; color: #606266; border-color: #dcdfe6; }
.dialog-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }
.cancel-button { background: #fff; color: #606266; border-color: #dcdfe6; }
p { font-size: 12px; color: #389660; margin: 8px 0 0; }
p.failed { color: #d64343; }
</style>
