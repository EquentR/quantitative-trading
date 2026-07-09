import { render, screen, waitFor, within, fireEvent } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createPinia, setActivePinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'
import { http, HttpResponse } from 'msw'
import { beforeEach, expect, test } from 'vitest'
import { applyAuthGuard, routes } from '@/router'
import { useSessionStore } from '@/stores/session'
import { server } from '@/test/server'
import { mockRecommendations, mockNotifications } from '@/mocks/handlers'
import AppShell from '@/app/AppShell.vue'
import RecommendationListPage from '@/features/recommendations/RecommendationListPage.vue'
import type { Recommendation, NotificationProcessingStatus } from '@/api/types'

beforeEach(() => {
  localStorage.clear()
})

function renderPage() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().setToken('test-token')
  return render(RecommendationListPage, { global: { plugins: [pinia, VueQueryPlugin] } })
}

async function renderShellAt(path: string) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().setToken('test-token')
  const router = createRouter({ history: createMemoryHistory(), routes })
  applyAuthGuard(router)
  await router.push(path)
  await router.isReady()
  return render(AppShell, { global: { plugins: [pinia, VueQueryPlugin, router] } })
}

test('路由页面渲染建议列表表头与模拟建议行', async () => {
  await renderShellAt('/recommendations')

  await waitFor(() => expect(screen.getByText('600000')).toBeInTheDocument())
  for (const header of ['股票', '动作', '置信度', '处理状态', '关键价位', '数据时间']) {
    expect(screen.getByText(header)).toBeInTheDocument()
  }
  expect(screen.getByText('示例银行')).toBeInTheDocument()
})

test('动作/置信度/处理状态徽章渲染期望中文标签', async () => {
  renderPage()

  await waitFor(() => expect(screen.getByText('持有')).toBeInTheDocument())
  expect(screen.getByText('中')).toBeInTheDocument()
  // Notification status badge appears once notifications query resolves.
  await waitFor(
    () => expect(screen.getAllByText('未读').length).toBeGreaterThan(0),
    { timeout: 3000 },
  )
})

test('点击行按钮打开详情抽屉并展示必要区块与数据时间', async () => {
  const user = userEvent.setup()
  renderPage()
  await screen.findByText('600000')

  await user.click(screen.getByRole('button', { name: /查看详情/ }))

  const drawer = await screen.findByRole('complementary', { name: '建议详情' })

  for (const section of ['理由', '风险', '失效条件', '仓位约束', '账户上下文', '持仓上下文', '数据引用', '审计引用']) {
    expect(within(drawer).getByText(section)).toBeInTheDocument()
  }
  expect(within(drawer).getByText('跌破 9.7')).toBeInTheDocument()
  expect(within(drawer).getByText('单票不超过配置上限')).toBeInTheDocument()
  expect(within(drawer).getByText('量价稳定，持仓观望')).toBeInTheDocument()
  expect(drawer).toHaveTextContent('audit-001')
  expect(drawer.getAttribute('data-data-time')).toBeTruthy()
})

test('扫描按钮触发 POST /api/v1/recommendations/scan 并刷新列表', async () => {
  const user = userEvent.setup()
  let scanCalled = false
  server.use(
    http.post('/api/v1/recommendations/scan', () => {
      scanCalled = true
      return HttpResponse.json({ count: mockRecommendations.length, recommendations: mockRecommendations })
    }),
  )
  renderPage()
  await screen.findByText('600000')

  await user.click(screen.getByRole('button', { name: '扫描建议' }))

  await waitFor(() => expect(scanCalled).toBe(true))
  await waitFor(() => expect(screen.getByText('600000')).toBeInTheDocument())
})

test('处理状态筛选可按未读/已读过滤列表', async () => {
  const user = userEvent.setup()
  renderPage()
  await screen.findByText('600000')

  // Wait for notifications to load so the filter becomes available.
  const filter = await screen.findByLabelText('处理状态筛选') as HTMLSelectElement
  await user.selectOptions(filter, 'unread')
  expect(screen.getByText('600000')).toBeInTheDocument()

  await user.selectOptions(filter, 'read')
  await waitFor(() => expect(screen.queryByText('600000')).not.toBeInTheDocument())
})

test('通知数据不可用时不展示处理状态筛选但页面仍可用', async () => {
  server.use(
    http.get('/api/v1/notifications', () =>
      HttpResponse.json({ error: { message: 'not mounted' } }, { status: 404 }),
    ),
  )
  renderPage()
  await screen.findByText('600000')

  expect(screen.queryByLabelText('处理状态筛选')).not.toBeInTheDocument()
  const row = screen.getByText('600000').closest('tr')!
  expect(within(row).getByText('不可用')).toBeInTheDocument()
})

test('缺失失效条件的建议详情用告警替代完整展示', async () => {
  const user = userEvent.setup()
  const brokenRec: Recommendation = {
    ...mockRecommendations[0],
    recommendation_id: 'rec-broken',
    risk: { notes: ['行情可能延迟'] },
  }
  server.use(
    http.get('/api/v1/recommendations', () => HttpResponse.json([brokenRec])),
    http.get('/api/v1/notifications', () => HttpResponse.json([])),
  )
  renderPage()
  await screen.findByRole('button', { name: /查看详情/ })

  await user.click(screen.getByRole('button', { name: /查看详情/ }))

  await waitFor(() =>
    expect(screen.getByText(/缺少必要字段/)).toBeInTheDocument(),
  )
  expect(screen.queryByText('失效条件')).not.toBeInTheDocument()
  expect(screen.queryByText('仓位约束')).not.toBeInTheDocument()
})

test('页面不包含真实下单或成交按钮文案', async () => {
  renderPage()
  await screen.findByText('600000')

  const buttons = screen.queryAllByRole('button')
  for (const btn of buttons) {
    expect(btn.textContent).not.toMatch(/立即买入|立即卖出|下单|成交确认|买入成功|卖出成功|交易成功/)
  }
})

test('左侧导航包含建议入口', async () => {
  await renderShellAt('/recommendations')
  expect(screen.getByRole('link', { name: '建议' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '移动导航 建议' })).toBeInTheDocument()
})
