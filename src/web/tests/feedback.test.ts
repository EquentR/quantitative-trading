import { render, screen, waitFor } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createPinia, setActivePinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { http, HttpResponse } from 'msw'
import { beforeEach, expect, test } from 'vitest'
import ReviewPage from '@/features/review/ReviewPage.vue'
import { useSessionStore } from '@/stores/session'
import { server } from '@/test/server'
import {
  mockRecommendations,
  mockNotifications,
  mockAuditLog,
  mockServiceStatus,
  mockTradingPlan,
  mockPositions,
  mockCashAccount,
} from '@/mocks/handlers'

beforeEach(() => {
  localStorage.clear()
})

function renderReview() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().setToken('test-token')
  return render(ReviewPage, { global: { plugins: [pinia, VueQueryPlugin] } })
}

test('复盘页展示人工执行反馈面板并包含记录按钮文案', async () => {
  renderReview()
  await waitFor(() => expect(screen.getByText('人工执行反馈')).toBeInTheDocument())
  expect(screen.getByRole('button', { name: '记录人工执行反馈' })).toBeInTheDocument()
})

test('提交反馈发送正确 POST 载荷', async () => {
  const user = userEvent.setup()
  let postBody: Record<string, unknown> | null = null
  let postCalled = false
  server.use(
    http.post('/api/v1/feedback', async ({ request }) => {
      postCalled = true
      postBody = (await request.json()) as Record<string, unknown>
      return HttpResponse.json(
        { ...postBody, feedback_id: 'fb-001', created_at: '2026-07-07T10:30:00+08:00' },
        { status: 201 },
      )
    }),
  )

  renderReview()
  await screen.findByText('人工执行反馈')
  // Wait for recommendations to load so the select has options.
  await waitFor(() => {
    const sel = screen.getByLabelText('建议选择') as HTMLSelectElement
    expect(sel.options.length).toBeGreaterThan(1)
  })

  const recSelect = screen.getByLabelText('建议选择') as HTMLSelectElement
  await user.selectOptions(recSelect, 'rec-001')

  const executedSelect = screen.getByLabelText('是否执行') as HTMLSelectElement
  await user.selectOptions(executedSelect, 'true')

  await user.type(screen.getByLabelText('执行价（可选）'), '10.5')
  await user.type(screen.getByLabelText('执行数量（可选）'), '100')
  await user.type(screen.getByLabelText('备注'), '按计划观察')

  await user.click(screen.getByRole('button', { name: '记录人工执行反馈' }))

  await waitFor(() => expect(postCalled).toBe(true))
  expect(postBody).toMatchObject({
    recommendation_id: 'rec-001',
    executed: true,
    execution_price: 10.5,
    execution_quantity: 100,
    note: '按计划观察',
  })
})

test('提交反馈成功后刷新建议/通知/审计/服务查询且不刷新持仓与资金', async () => {
  const user = userEvent.setup()
  let recGetCount = 0
  let notifGetCount = 0
  let auditGetCount = 0
  let serviceGetCount = 0
  let plansGetCount = 0
  let posGetCount = 0
  let cashGetCount = 0
  let feedbackPostCount = 0

  server.use(
    http.get('/api/v1/recommendations', () => {
      recGetCount++
      return HttpResponse.json(mockRecommendations)
    }),
    http.get('/api/v1/notifications', () => {
      notifGetCount++
      return HttpResponse.json(mockNotifications)
    }),
    http.get('/api/v1/audit', () => {
      auditGetCount++
      return HttpResponse.json([mockAuditLog])
    }),
    http.get('/api/v1/service/status', () => {
      serviceGetCount++
      return HttpResponse.json(mockServiceStatus)
    }),
    http.get('/api/v1/plans/latest', () => {
      plansGetCount++
      return HttpResponse.json(mockTradingPlan)
    }),
    http.get('/api/v1/positions', () => {
      posGetCount++
      return HttpResponse.json(mockPositions)
    }),
    http.get('/api/v1/cash/account', () => {
      cashGetCount++
      return HttpResponse.json(mockCashAccount)
    }),
    http.post('/api/v1/feedback', async ({ request }) => {
      feedbackPostCount++
      const body = (await request.json()) as Record<string, unknown>
      return HttpResponse.json(
        { ...body, feedback_id: 'fb-001', created_at: '2026-07-07T10:30:00+08:00' },
        { status: 201 },
      )
    }),
  )

  renderReview()
  // Wait for initial fetches to settle: feedback panel visible.
  await screen.findByText('人工执行反馈')
  // Wait for recommendations to load so the select has options.
  await waitFor(() => {
    const sel = screen.getByLabelText('建议选择') as HTMLSelectElement
    expect(sel.options.length).toBeGreaterThan(1)
  })

  const recBefore = recGetCount
  const notifBefore = notifGetCount
  const auditBefore = auditGetCount
  const serviceBefore = serviceGetCount
  const plansBefore = plansGetCount
  const posBefore = posGetCount
  const cashBefore = cashGetCount

  const recSelect = screen.getByLabelText('建议选择') as HTMLSelectElement
  await user.selectOptions(recSelect, 'rec-001')
  const executedSelect = screen.getByLabelText('是否执行') as HTMLSelectElement
  await user.selectOptions(executedSelect, 'true')
  await user.type(screen.getByLabelText('备注'), '测试')
  await user.click(screen.getByRole('button', { name: '记录人工执行反馈' }))

  await waitFor(() => expect(feedbackPostCount).toBe(1))

  // Refetches for recommendations, notifications, audit, service.
  await waitFor(() => expect(recGetCount).toBeGreaterThan(recBefore))
  await waitFor(() => expect(notifGetCount).toBeGreaterThan(notifBefore))
  await waitFor(() => expect(auditGetCount).toBeGreaterThan(auditBefore))
  await waitFor(() => expect(serviceGetCount).toBeGreaterThan(serviceBefore))
  await waitFor(() => expect(plansGetCount).toBeGreaterThan(plansBefore))

  // Positions and cash not refetched.
  expect(posGetCount).toBe(posBefore)
  expect(cashGetCount).toBe(cashBefore)
})

test('提交反馈失败时展示错误告警', async () => {
  const user = userEvent.setup()
  server.use(
    http.post('/api/v1/feedback', () =>
      HttpResponse.json({ error: { message: 'feedback failed' } }, { status: 500 }),
    ),
  )
  renderReview()
  await screen.findByText('人工执行反馈')
  await waitFor(() => {
    const sel = screen.getByLabelText('建议选择') as HTMLSelectElement
    expect(sel.options.length).toBeGreaterThan(1)
  })

  const recSelect = screen.getByLabelText('建议选择') as HTMLSelectElement
  await user.selectOptions(recSelect, 'rec-001')
  const executedSelect = screen.getByLabelText('是否执行') as HTMLSelectElement
  await user.selectOptions(executedSelect, 'false')
  await user.type(screen.getByLabelText('备注'), '未执行')
  await user.click(screen.getByRole('button', { name: '记录人工执行反馈' }))

  await waitFor(() =>
    expect(screen.getByText(/反馈提交失败/)).toBeInTheDocument(),
  )
})

test('反馈面板不包含真实交易成功文案', async () => {
  renderReview()
  await screen.findByText('人工执行反馈')
  const buttons = screen.queryAllByRole('button')
  for (const btn of buttons) {
    expect(btn.textContent).not.toMatch(/成交成功|买入成功|卖出成功|交易成功/)
  }
})
