import { createApp } from 'vue'
import { watch } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import App from './App.vue'
import router from './router'
import { useFeatureAccessStore } from '@/store/featureAccessStore'
import { useUserStore } from '@/store/userStore'
import { markBootPerf } from '@/utils/bootPerf'
import { resetNoteTypePaletteState } from '@/utils/noteTypePaletteState'

markBootPerf('main.module')
const app = createApp(App)
const pinia = createPinia()

markBootPerf('main.before-use')
app.use(pinia)
app.use(router)
markBootPerf('main.after-use')

// Initialize User Store
const userStore = useUserStore(pinia)
const featureAccessStore = useFeatureAccessStore(pinia)
userStore.initialize()
markBootPerf('main.user-store-initialized')

watch(
  () => userStore.token,
  (_token, oldToken) => {
    const loadContext = oldToken === undefined
      ? featureAccessStore.ensureLoaded()
      : featureAccessStore.refreshContext()
    loadContext.catch(error => {
      console.warn('Failed to load feature access context:', error?.message || error)
    })
  },
  { immediate: true }
)

watch(
  () => userStore.token,
  token => {
    if (!token) resetNoteTypePaletteState()
  },
  { immediate: true }
)

markBootPerf('main.before-mount')
app.mount('#app')
markBootPerf('main.after-mount')
