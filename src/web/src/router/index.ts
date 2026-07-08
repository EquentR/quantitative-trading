import type { RouteRecordRaw } from 'vue-router'
import { createRouter, createWebHistory } from 'vue-router'
import { useSessionStore } from '@/stores/session'
import DashboardPage from '@/features/dashboard/DashboardPage.vue'
import PreparationPage from '@/features/preparation/PreparationPage.vue'
import MonitoringPage from '@/features/monitoring/MonitoringPage.vue'
import ReviewPage from '@/features/review/ReviewPage.vue'
import SettingsPage from '@/features/settings/SettingsPage.vue'
import LoginPage from '@/features/auth/LoginPage.vue'
import SetupPage from '@/features/auth/SetupPage.vue'

export const routes: RouteRecordRaw[] = [
  { path: '/', component: DashboardPage, meta: { auth: true, name: '今日仪表盘' } },
  { path: '/prepare', component: PreparationPage, meta: { auth: true, name: '准备' } },
  { path: '/monitor', component: MonitoringPage, meta: { auth: true, name: '监控' } },
  { path: '/review', component: ReviewPage, meta: { auth: true, name: '复盘' } },
  { path: '/settings', component: SettingsPage, meta: { auth: true, name: '设置' } },
  { path: '/login', component: LoginPage, meta: { name: '登录' } },
  { path: '/setup', component: SetupPage, meta: { name: '设置访问密码' } },
]

export function applyAuthGuard(router: ReturnType<typeof createRouter>) {
  router.beforeEach((to) => {
    if (!to.meta.auth) return true
    const session = useSessionStore()
    if (session.token) return true
    return { path: '/login', query: { redirect: to.fullPath } }
  })
}

export function createAppRouter() {
  const router = createRouter({ history: createWebHistory(), routes })
  applyAuthGuard(router)
  return router
}
