import { createApp } from 'vue'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { createPinia } from 'pinia'
import App from './App.vue'
import { createAppRouter } from './router'
import './styles.css'

const app = createApp(App)
app.use(createPinia())
app.use(VueQueryPlugin)
app.use(createAppRouter())
app.mount('#app')
