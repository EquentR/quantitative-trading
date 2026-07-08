import { render, screen } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'
import { expect, test } from 'vitest'
import AppShell from '@/app/AppShell.vue'
import { applyAuthGuard, routes } from '@/router'
import { useSessionStore } from '@/stores/session'

async function renderShell() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().setToken('test-token')
  const router = createRouter({ history: createMemoryHistory(), routes })
  applyAuthGuard(router)
  await router.push('/')
  await router.isReady()
  return render(AppShell, { global: { plugins: [pinia, VueQueryPlugin, router] } })
}

test('左侧导航包含主要入口', async () => {
  await renderShell()

  expect(screen.getByRole('link', { name: '今日仪表盘' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '准备' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '监控' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '复盘' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '设置' })).toBeInTheDocument()
})

test('顶部安全文案显示本地决策辅助说明', async () => {
  await renderShell()

  expect(screen.getByText('只做本地决策辅助，不自动真实下单')).toBeInTheDocument()
})

test('窄屏也提供移动导航入口', async () => {
  await renderShell()

  expect(screen.getByRole('link', { name: '移动导航 准备' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '移动导航 监控' })).toBeInTheDocument()
})
