import { render, screen, waitFor } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createPinia, setActivePinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'
import { http, HttpResponse } from 'msw'
import { beforeEach, expect, test } from 'vitest'
import ReviewPage from '@/features/review/ReviewPage.vue'
import SettingsPage from '@/features/settings/SettingsPage.vue'
import { mockTradingPlan } from '@/mocks/handlers'
import { server } from '@/test/server'
import { useSessionStore } from '@/stores/session'

beforeEach(() => {
  localStorage.clear()
})

async function renderReview(path = '/review') {
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().setToken('test-token')
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/review', component: ReviewPage },
      { path: '/market', component: { template: '<div>行情目标</div>' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  return {
    ...render(ReviewPage, { global: { plugins: [pinia, VueQueryPlugin, router] } }),
    router,
  }
}

test('复盘页展示推荐记录和人工执行反馈且不出现买入成功', async () => {
  await renderReview()

  await waitFor(() => expect(screen.getByText('推荐记录')).toBeInTheDocument())
  expect(screen.getByText('人工执行反馈')).toBeInTheDocument()
  expect(screen.getByText('审计日志')).toBeInTheDocument()
  expect(screen.queryByText('买入成功')).not.toBeInTheDocument()
})

test('复盘页可在当前状态与历史记录间切换', async () => {
  const user = userEvent.setup()
  const recommendationViews: string[] = []
  const notificationViews: string[] = []
  server.use(
    http.get('/api/v1/recommendations', ({ request }) => {
      const view = new URL(request.url).searchParams.get('view') ?? ''
      recommendationViews.push(view)
      return HttpResponse.json({ items: [], total: 0, page: 1, page_size: 20 })
    }),
    http.get('/api/v1/notifications', ({ request }) => {
      const view = new URL(request.url).searchParams.get('view') ?? ''
      notificationViews.push(view)
      return HttpResponse.json({ items: [], total: 0, page: 1, page_size: 50 })
    }),
  )
  await renderReview()

  expect(await screen.findByRole('button', { name: '当前状态' })).toHaveAttribute('aria-pressed', 'true')
  await user.click(screen.getByRole('button', { name: '历史记录' }))
  await waitFor(() => {
    expect(recommendationViews).toContain('history')
    expect(notificationViews).toContain('history')
  })
})

test('plan_id query 定位指定计划并可按计划标的返回行情', async () => {
  const user = userEvent.setup()
  server.use(
    http.get('/api/v1/plans/plan-target', () =>
      HttpResponse.json({ ...mockTradingPlan, plan_id: 'plan-target' }),
    ),
  )
  const { router } = await renderReview('/review?plan_id=plan-target')

  expect(await screen.findByRole('status')).toHaveTextContent('已定位计划 plan-target')
  await user.click(screen.getByRole('link', { name: '返回 600000 行情（计划 plan-target）' }))
  expect(router.currentRoute.value.path).toBe('/market')
  expect(router.currentRoute.value.query.symbol).toBe('600000')
})

test('设置页保存 API 地址到 localStorage', async () => {
  const user = userEvent.setup()
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().setToken('test-token')
  const router = createRouter({ history: createMemoryHistory(), routes: [] })
  render(SettingsPage, { global: { plugins: [pinia, VueQueryPlugin, router] } })

  await user.clear(screen.getByLabelText('API 地址'))
  await user.type(screen.getByLabelText('API 地址'), 'http://127.0.0.1:9000')
  await user.click(screen.getByRole('button', { name: '保存本地设置' }))

  await waitFor(() =>
    expect(localStorage.getItem('qt_console_api_base_url')).toBe('http://127.0.0.1:9000'),
  )
})

test('设置页退出登录只清除前端 token', async () => {
  const user = userEvent.setup()
  const pinia = createPinia()
  setActivePinia(pinia)
  const session = useSessionStore()
  session.setToken('test-token')
  session.setApiBaseUrl('http://127.0.0.1:9000')
  const router = createRouter({ history: createMemoryHistory(), routes: [] })
  render(SettingsPage, { global: { plugins: [pinia, VueQueryPlugin, router] } })

  await user.click(screen.getByRole('button', { name: '退出登录' }))

  await waitFor(() => expect(session.token).toBeNull())
  expect(localStorage.getItem('qt_console_access_token')).toBeNull()
  expect(session.apiBaseUrl).toBe('http://127.0.0.1:9000')
  expect(screen.getByText(/不保存明文访问密码/)).toBeInTheDocument()
})
