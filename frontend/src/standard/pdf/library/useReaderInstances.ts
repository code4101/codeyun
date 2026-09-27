import { onBeforeUnmount, ref, watch, type Ref } from 'vue'

const IDLE_TIMEOUT = 30 * 60 * 1000

/** 标签是持久入口，阅读器实例是临时缓存；只回收离开阅读状态超过半小时的实例。 */
export function useReaderInstances(keys: Ref<string[]>, active: Ref<string>, shown: Ref<boolean>) {
  const mounted = ref(new Set<string>())
  const idleSince = new Map<string, number>()
  watch(() => [keys.value, active.value, shown.value] as const, ([tabs, current, visible]) => {
    const existing = new Set(tabs)
    for (const key of mounted.value) {
      if (!existing.has(key)) {
        mounted.value.delete(key)
        idleSince.delete(key)
      } else if (visible && key === current) idleSince.delete(key)
      else if (!idleSince.has(key)) idleSince.set(key, Date.now())
    }
    if (visible && existing.has(current)) mounted.value.add(current)
  }, { immediate: true })

  // 后台浏览器会节流定时器，因此按实际经过时间判断，不能累计 tick 次数。
  const timer = setInterval(() => {
    const now = Date.now()
    for (const [key, since] of idleSince) {
      if (now - since >= IDLE_TIMEOUT) {
        mounted.value.delete(key)
        idleSince.delete(key)
      }
    }
  }, 60 * 1000)
  onBeforeUnmount(() => clearInterval(timer))
  return mounted
}
