import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import App from './App.vue'
import router from './router'
import { useUserStore } from '@/store/userStore'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)

// Initialize User Store
const userStore = useUserStore(pinia)
userStore.initialize()

app.mount('#app')
