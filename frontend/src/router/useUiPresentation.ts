import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { resolveUiPresentation } from './uiPresentation'

/** 响应 URL 变化；隐藏外壳时不替换业务组件，也不修改用户保存的工作区布局。 */
export function useUiPresentation() {
  const route = useRoute()
  const presentation = computed(() => resolveUiPresentation(route.query, route.meta))
  return {
    ui: computed(() => presentation.value.ui),
    showAppNavigation: computed(() => presentation.value.showAppNavigation),
    showWorkbench: computed(() => presentation.value.showWorkbench),
  }
}
