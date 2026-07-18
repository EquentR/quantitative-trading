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

function linked(recommendation: Recommendation, status: NotificationProcessingStatus | null = null) {
  return {
    recommendation,
    notification: status === null ? null : {
      notification_id: `notif-${recommendation.recommendation_id}`,
      status,
    },
  }
}

beforeEach(() => {
  localStorage.clear()
})

async function renderPage(path = '/recommendations') {
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().setToken('test-token')
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/recommendations', component: RecommendationListPage },
      { path: '/market', component: { template: '<div>行情目标</div>' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  return {
    ...render(RecommendationListPage, { global: { plugins: [pinia, VueQueryPlugin, router] } }),
    router,
  }
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

test('建议页默认 current 并可切换 history linked projection', async () => {
  const user = userEvent.setup()
  const views: string[] = []
  server.use(
    http.get('/api/v1/recommendations', ({ request }) => {
      const view = new URL(request.url).searchParams.get('view') ?? ''
      views.push(view)
      const recommendation = view === 'history'
        ? { ...mockRecommendations[0], recommendation_id: 'rec-history' }
        : mockRecommendations[0]
      return HttpResponse.json({
        items: [{ recommendation, notification: {
          notification_id: view === 'history' ? 'notif-history' : 'notif-001',
          status: view === 'history' ? 'read' : 'unread',
        } }],
        total: 1,
        page: 1,
        page_size: 20,
      })
    }),
  )
  await renderPage()

  expect(await screen.findByRole('button', { name: '当前状态' })).toHaveAttribute('aria-pressed', 'true')
  expect((await screen.findAllByText('未读')).length).toBeGreaterThan(0)
  await user.click(screen.getByRole('button', { name: '历史记录' }))

  expect((await screen.findAllByText('已读')).length).toBeGreaterThan(0)
  expect(views).toContain('current')
  expect(views).toContain('history')
})

test('切换 current/history 时清理旧处理筛选和详情定位', async () => {
  const user = userEvent.setup()
  server.use(
    http.get('/api/v1/recommendations', ({ request }) => {
      const view = new URL(request.url).searchParams.get('view')
      const recommendation = view === 'history'
        ? { ...mockRecommendations[0], recommendation_id: 'rec-history', symbol: '600519' }
        : mockRecommendations[0]
      return HttpResponse.json({
        items: [linked(recommendation, view === 'history' ? null : 'unread')],
        total: 1, page: 1, page_size: 20,
      })
    }),
  )
  const { router } = await renderPage()
  await screen.findByText('600000')
  await user.selectOptions(screen.getByLabelText('处理状态筛选'), 'unread')
  await user.click(screen.getByRole('button', { name: /查看详情 600000/ }))
  await screen.findByRole('dialog', { name: '建议详情' })

  await user.click(screen.getByRole('button', { name: '历史记录' }))

  expect(await screen.findByText('600519')).toBeInTheDocument()
  expect(screen.queryByLabelText('处理状态筛选')).not.toBeInTheDocument()
  expect(screen.queryByRole('dialog', { name: '建议详情' })).not.toBeInTheDocument()
  expect(router.currentRoute.value.query.recommendation_id).toBeUndefined()
})

test('动作/置信度/处理状态徽章渲染期望中文标签', async () => {
  await renderPage()

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
  await renderPage()
  await screen.findByText('600000')

  await user.click(screen.getByRole('button', { name: /查看详情/ }))

  const drawer = await screen.findByRole('dialog', { name: '建议详情' })

  for (const section of ['理由', '风险', '失效条件', '仓位约束', '账户上下文', '持仓上下文', '数据引用', '审计引用']) {
    expect(within(drawer).getByText(section)).toBeInTheDocument()
  }
  expect(within(drawer).getByText('跌破 9.7')).toBeInTheDocument()
  expect(within(drawer).getByText('单票不超过配置上限')).toBeInTheDocument()
  expect(within(drawer).getByText('量价稳定，持仓观望')).toBeInTheDocument()
  expect(drawer).toHaveTextContent('audit-001')
  expect(drawer.getAttribute('data-data-time')).toBeTruthy()
})

test('recommendation_id query 自动定位详情并可返回对应行情', async () => {
  const user = userEvent.setup()
  const { router } = await renderPage('/recommendations?recommendation_id=rec-001')

  const drawer = await screen.findByRole('dialog', { name: '建议详情' })
  await user.click(within(drawer).getByRole('link', { name: '返回 600000 行情' }))
  expect(router.currentRoute.value.path).toBe('/market')
  expect(router.currentRoute.value.query.symbol).toBe('600000')
})

test('建议页刷新使用认证的两阶段工作流', async () => {
  const user = userEvent.setup()
  let workflowCalled = false
  let deprecatedScanCalled = false
  server.use(
    http.post('/api/v1/service/workflows/intraday/run', ({ request }) => {
      expect(request.headers.get('authorization')).toBe('Bearer test-token')
      workflowCalled = true
      return HttpResponse.json({
        task: 'intraday', status: 'success', run_id: 'intraday-test', snapshot_id: 1,
        plan_id: null, recommendation_ids: ['rec-001'], warnings: ['分钟数据使用降级源'], reused: true,
        ready: null, cleaned_rows: null, mode: 'decision',
        effective_trade_date: '2026-07-17', history_cutoff_date: '2026-07-16',
        requested_symbol_scope: ['600000', '600519'], lease_expires_at: null,
      })
    }),
    http.post('/api/v1/recommendations/scan', () => {
      deprecatedScanCalled = true
      return HttpResponse.json({ error: { code: 'gone', message: 'gone' } }, { status: 410 })
    }),
  )
  await renderPage()
  await screen.findByText('600000')

  await user.click(screen.getByRole('button', { name: '刷新行情与建议' }))

  await waitFor(() => expect(workflowCalled).toBe(true))
  expect(deprecatedScanCalled).toBe(false)
  expect(screen.getByRole('status')).toHaveTextContent('行情与建议已刷新')
  const stages = screen.getByRole('region', { name: '行情刷新阶段详情' })
  expect(within(stages).getByText('backfill-mock-run')).toBeInTheDocument()
  expect(within(stages).getByText('intraday-test')).toBeInTheDocument()
  expect(within(stages).getByText('分钟数据使用降级源')).toBeInTheDocument()
  expect(within(stages).getByText('复用已有运行')).toBeInTheDocument()
  await waitFor(() => expect(screen.getByText('600000')).toBeInTheDocument())
})

test('报价分时阶段失败时保留日 K 部分成功提示且页面仍可用', async () => {
  const user = userEvent.setup()
  server.use(
    http.post('/api/v1/service/workflows/intraday/run', () =>
      HttpResponse.json({ error: { message: 'scan failed' } }, { status: 500 }),
    ),
  )
  await renderPage()
  await screen.findByText('600000')

  await user.click(screen.getByRole('button', { name: '刷新行情与建议' }))

  await waitFor(() =>
    expect(screen.getByText('日 K 已更新，报价/分时刷新失败')).toBeInTheDocument(),
  )
  // Page remains usable: list still visible, refresh button re-enabled.
  expect(screen.getByText('600000')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '刷新行情与建议' })).not.toBeDisabled()
})

test('处理状态筛选可按未读/已读过滤列表', async () => {
  const user = userEvent.setup()
  await renderPage()
  await screen.findByText('600000')

  // Wait for notifications to load so the filter becomes available.
  const filter = await screen.findByLabelText('处理状态筛选') as HTMLSelectElement
  await user.selectOptions(filter, 'unread')
  expect(screen.getByText('600000')).toBeInTheDocument()

  await user.selectOptions(filter, 'read')
  await waitFor(() => expect(screen.queryByText('600000')).not.toBeInTheDocument())
})

test('linked DTO 无通知时不展示处理状态筛选但页面仍可用', async () => {
  server.use(
    http.get('/api/v1/recommendations', () => HttpResponse.json({
      items: [linked(mockRecommendations[0])], total: 1, page: 1, page_size: 20,
    })),
  )
  await renderPage()
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
    http.get('/api/v1/recommendations', () => HttpResponse.json({
      items: [linked(brokenRec)], total: 1, page: 1, page_size: 20,
    })),
  )
  await renderPage()
  await screen.findByRole('button', { name: /查看详情/ })

  await user.click(screen.getByRole('button', { name: /查看详情/ }))

  await waitFor(() =>
    expect(screen.getByText(/缺少必要字段/)).toBeInTheDocument(),
  )
  expect(screen.queryByText('失效条件')).not.toBeInTheDocument()
  expect(screen.queryByText('仓位约束')).not.toBeInTheDocument()
})

test('详情抽屉可按 role dialog 与名称建议详情定位', async () => {
  const user = userEvent.setup()
  await renderPage()
  await screen.findByText('600000')

  await user.click(screen.getByRole('button', { name: /查看详情/ }))

  const dialog = await screen.findByRole('dialog', { name: '建议详情' })
  expect(dialog).toBeInTheDocument()
  expect(dialog.getAttribute('aria-modal')).toBe('true')
})

test('按 Escape 关闭详情抽屉', async () => {
  const user = userEvent.setup()
  await renderPage()
  await screen.findByText('600000')

  await user.click(screen.getByRole('button', { name: /查看详情/ }))

  await screen.findByRole('dialog', { name: '建议详情' })
  await user.keyboard('{Escape}')

  await waitFor(() => expect(screen.queryByRole('dialog', { name: '建议详情' })).not.toBeInTheDocument())
})

test('打开详情抽屉后焦点移至关闭按钮', async () => {
  const user = userEvent.setup()
  await renderPage()
  await screen.findByText('600000')

  await user.click(screen.getByRole('button', { name: /查看详情/ }))

  const closeBtn = await screen.findByRole('button', { name: '关闭详情' })
  await waitFor(() => expect(closeBtn).toHaveFocus())
})

test('sell 建议缺少 invalid_if 不触发契约错误并展示不适用', async () => {
  const user = userEvent.setup()
  const sellRec: Recommendation = {
    ...mockRecommendations[0],
    recommendation_id: 'rec-sell',
    action: 'sell',
    reason: ['止损线触发'],
    risk: { notes: [] },
    position_context: {},
    account_context: {},
    price_context: {},
  }
  server.use(
    http.get('/api/v1/recommendations', () => HttpResponse.json({
      items: [linked(sellRec)], total: 1, page: 1, page_size: 20,
    })),
  )
  await renderPage()
  await screen.findByRole('button', { name: /查看详情/ })

  await user.click(screen.getByRole('button', { name: /查看详情/ }))

  const dialog = await screen.findByRole('dialog', { name: '建议详情' })

  // No contract error for sell without invalid_if.
  expect(within(dialog).queryByText(/缺少必要字段/)).not.toBeInTheDocument()

  // Fallback text for optional empty invalid_if.
  expect(within(dialog).getByText('不适用')).toBeInTheDocument()

  // Fallback text for empty reason is NOT expected here (reason is non-empty).
  // Fallback text for empty context sections.
  expect(within(dialog).getByText('价格上下文不可用')).toBeInTheDocument()
  expect(within(dialog).getByText('账户上下文不可用')).toBeInTheDocument()
  expect(within(dialog).getByText('持仓上下文不可用')).toBeInTheDocument()
})

test('空理由列表展示暂无理由占位', async () => {
  const user = userEvent.setup()
  const emptyReasonRec: Recommendation = {
    ...mockRecommendations[0],
    recommendation_id: 'rec-noreason',
    action: 'avoid',
    reason: [],
    risk: { invalid_if: ['不适用'], notes: [] },
    position_context: {},
    account_context: {},
    price_context: {},
  }
  server.use(
    http.get('/api/v1/recommendations', () => HttpResponse.json({
      items: [linked(emptyReasonRec)], total: 1, page: 1, page_size: 20,
    })),
  )
  await renderPage()
  await screen.findByRole('button', { name: /查看详情/ })

  await user.click(screen.getByRole('button', { name: /查看详情/ }))

  const dialog = await screen.findByRole('dialog', { name: '建议详情' })
  expect(within(dialog).getByText('暂无理由')).toBeInTheDocument()
})

test('页面不包含真实下单或成交按钮文案', async () => {
  await renderPage()
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
