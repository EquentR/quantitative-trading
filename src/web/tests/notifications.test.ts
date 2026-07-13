import { render, screen, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'
import { http, HttpResponse } from 'msw'
import { beforeEach, expect, test } from 'vitest'
import DashboardPage from '@/features/dashboard/DashboardPage.vue'
import ReviewPage from '@/features/review/ReviewPage.vue'
import { useSessionStore } from '@/stores/session'
import { server } from '@/test/server'
import { mockNotifications, mockAuditLog } from '@/mocks/handlers'
import type { NotificationSummary, AuditLog } from '@/api/types'

beforeEach(() => {
  localStorage.clear()
})

function renderDashboard() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().setToken('test-token')
  return render(DashboardPage, { global: { plugins: [pinia, VueQueryPlugin] } })
}

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
  return render(ReviewPage, { global: { plugins: [pinia, VueQueryPlugin, router] } })
}

test('仪表盘展示通知摘要含未读与待反馈计数', async () => {
  renderDashboard()
  await waitFor(() => expect(screen.getByText('未读: 1')).toBeInTheDocument())
  expect(screen.getByText('待反馈: 1')).toBeInTheDocument()
})

test('复盘页展示通知摘要和审计日志', async () => {
  await renderReview()
  await waitFor(() => expect(screen.getByText('审计日志')).toBeInTheDocument())
  await waitFor(() => expect(screen.getByText(mockAuditLog.audit_id)).toBeInTheDocument())
})

test('复盘页展示推荐记录', async () => {
  await renderReview()
  await waitFor(() => expect(screen.getByText('推荐记录')).toBeInTheDocument())
})

test('复盘页推荐记录展示理由、风险、失效条件和有效期', async () => {
  await renderReview()

  await waitFor(() =>
    expect(screen.getByText('量价稳定，持仓观望 / 成交额放大 / 资金流转正')).toBeInTheDocument(),
  )
  expect(screen.getByText(/单票不超过配置上限/)).toBeInTheDocument()
  expect(screen.getByText(/行情数据可能延迟/)).toBeInTheDocument()
  expect(screen.getByText('跌破 9.7 / 资金流重新转负')).toBeInTheDocument()
  expect(screen.getByText('有效期')).toBeInTheDocument()
})

test('复盘建议、通知和审计记录按可用 symbol 返回行情', async () => {
  await renderReview()

  expect(await screen.findByRole('link', { name: '返回 600000 行情（建议 rec-001）' }))
    .toHaveAttribute('href', '/market?symbol=600000')
  expect(await screen.findByRole('link', { name: '返回 600000 行情（通知 notif-001）' }))
    .toHaveAttribute('href', '/market?symbol=600000')
  expect(await screen.findByRole('link', { name: '返回 600000 行情（审计 audit-001）' }))
    .toHaveAttribute('href', '/market?symbol=600000')
})

test('通知不可用时仪表盘仍可用并显示降级文案', async () => {
  server.use(
    http.get('/api/v1/notifications', () =>
      HttpResponse.json({ error: { message: 'not mounted' } }, { status: 404 }),
    ),
  )
  renderDashboard()
  await waitFor(() => expect(screen.getByText('通知数据不可用')).toBeInTheDocument())
  // Page still usable: other sections visible.
  expect(screen.getByText('建议摘要')).toBeInTheDocument()
})

test('通知不可用时复盘页仍可用并显示降级文案', async () => {
  server.use(
    http.get('/api/v1/notifications', () =>
      HttpResponse.json({ error: { message: 'not mounted' } }, { status: 404 }),
    ),
  )
  await renderReview()
  await waitFor(() => expect(screen.getByText('通知数据不可用')).toBeInTheDocument())
  // Page still usable.
  expect(screen.getByText('推荐记录')).toBeInTheDocument()
})

test('审计日志不可用时复盘页仍可用并显示降级文案', async () => {
  server.use(
    http.get('/api/v1/audit', () =>
      HttpResponse.json({ error: { message: 'not mounted' } }, { status: 404 }),
    ),
  )
  await renderReview()
  await waitFor(() => expect(screen.getByText('审计日志数据不可用')).toBeInTheDocument())
  // Page still usable.
  expect(screen.getByText('推荐记录')).toBeInTheDocument()
})

test('多条通知时统计未读与待反馈计数', async () => {
  const multiNotifications: NotificationSummary[] = [
    { ...mockNotifications[0], notification_id: 'n1', recommendation_id: 'r1', status: 'unread' },
    { ...mockNotifications[0], notification_id: 'n2', recommendation_id: 'r2', status: 'read' },
    { ...mockNotifications[0], notification_id: 'n3', recommendation_id: 'r3', status: 'feedback_recorded' },
    { ...mockNotifications[0], notification_id: 'n4', recommendation_id: 'r4', status: 'unread' },
  ]
  server.use(
    http.get('/api/v1/notifications', () => HttpResponse.json(multiNotifications)),
  )
  renderDashboard()
  await waitFor(() => expect(screen.getByText('未读: 2')).toBeInTheDocument())
  // 3 pending (not feedback_recorded)
  expect(screen.getByText('待反馈: 3')).toBeInTheDocument()
})
