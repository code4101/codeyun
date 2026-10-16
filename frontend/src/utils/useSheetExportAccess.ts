import { ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/store/userStore'

/** 导出入口保持可见；在点击时解释身份与权限，并提供返回原页的登录路径。 */
export function useSheetExportAccess() {
  const userStore = useUserStore()
  const router = useRouter()

  async function ensureExportAccess(allowed = true, status?: number) {
    if (!userStore.isAuthenticated || status === 401) {
      try {
        await ElMessageBox.confirm('请登录有导出权限的账号，登录后返回当前表格。', '登录后导出', {
          confirmButtonText: '去登录',
          cancelButtonText: '取消',
          type: 'info',
        })
        await router.push({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } })
      } catch {
        // 取消登录时保留当前表格。
      }
      return false
    }
    if (!allowed || status === 403) {
      const account = userStore.user?.username || userStore.user?.nickname || '已登录账号'
      await ElMessageBox.alert(`当前账号 ${account} 无导出权限，请找管理员申请。`, '无法导出', {
        confirmButtonText: '知道了',
        type: 'warning',
      }).catch(() => {})
      return false
    }
    return true
  }

  return { ensureExportAccess }
}
