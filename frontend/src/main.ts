import { createApp } from 'vue'
import { watch } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import App from './App.vue'
import router from './router'
import { useUserStore } from '@/store/userStore'
import { ensureNoteTypePaletteLoaded, resetNoteTypePaletteState } from '@/utils/nodeConfig'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)

// Initialize User Store
const userStore = useUserStore(pinia)
userStore.initialize()

watch(
  () => userStore.token,
  token => {
    if (token) {
      ensureNoteTypePaletteLoaded(true).catch(error => {
        console.warn('Failed to load note category palette:', error)
      })
      return
    }
    resetNoteTypePaletteState()
  },
  { immediate: true }
)

app.mount('#app')
