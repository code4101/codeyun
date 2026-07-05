import { createApp } from 'vue'
import { watch } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import App from './App.vue'
import router from './router'
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
userStore.initialize()
markBootPerf('main.user-store-initialized')

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
