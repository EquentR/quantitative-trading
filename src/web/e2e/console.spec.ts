import { expect, test, type Page } from '@playwright/test'

const now = '2026-07-07T10:30:00+08:00'

const serviceStatus = {
  auth_status: 'configured',
  scheduler_enabled: false,
  scheduler_running: false,
  interval_seconds: 180,
  timezone: 'Asia/Shanghai',
  run_on_start: true,
  next_run_time: null,
  last_started_at: '2026-07-07T10:29:00+08:00',
  last_finished_at: now,
  last_status: 'success',
  last_reason: 'manual_api',
  last_error: null,
  last_snapshot_id: 1,
  last_task_type: 'account_snapshot',
  last_plan_id: null,
  last_recommendation_ids: [],
}

const positions = [
  {
    symbol: '600000',
    name: '示例银行',
    quantity: 1000,
    available_quantity: 1000,
    cost_price: 9.5,
    opened_at: '2026-07-01',
    note: '测试持仓',
    updated_at: now,
  },
]

const cashAccount = {
  cash_balance: 48000,
  total_transfer_in: 50000,
  total_transfer_out: 0,
  net_principal: 50000,
  updated_at: now,
}

const accountSnapshot = {
  cash_balance: 48000,
  net_principal: 50000,
  market_value: 10500,
  position_cost: 9500,
  floating_pnl: 1000,
  floating_pnl_pct: 0.1053,
  total_assets: 58500,
  total_pnl: 8500,
  total_pnl_pct: 0.17,
  position_ratio: 0.1795,
  available_buying_cash: 48000,
  positions: [],
  status: 'ok',
  warnings: [],
  created_at: now,
}

const watchPinned = [
  {
    symbol: '600519',
    name: '示例白酒',
    rank: 1,
    plan_enabled: true,
    source: 'manual',
    note: '核心自选',
    updated_at: now,
  },
]

const plan = {
  plan_id: 'plan-001',
  trading_day: '2026-07-07',
  generated_at: now,
  valid_until: '2026-07-08T15:00:00+08:00',
  universe_snapshot_id: 1,
  account_snapshot_id: 1,
  ledger_max_updated_at: now,
  watch_symbols: ['600519'],
  holding_symbols: ['600000'],
  key_levels: { '600000': { stop_loss: 9.0, resistance: 11.0 } },
  candidate_actions: { '600000': ['hold'] },
  invalid_if: { '600000': ['跌破9.0'] },
  warnings: [],
  status: 'active',
}

const recommendations = [
  {
    recommendation_id: 'rec-001',
    symbol: '600000',
    name: '示例银行',
    action: 'hold',
    confidence: 'medium',
    position_context: {
      source: 'manual_ledger',
      ledger_updated_at: '2026-07-07T09:00:00+08:00',
      cost_price: 9.5,
      quantity: 1000,
      available_quantity: 1000,
    },
    account_context: {
      source: 'manual_cash_account',
      cash_balance: 48000,
      net_principal: 50000,
      market_value: 10500,
      total_assets: 58500,
      position_ratio: 0.18,
      account_snapshot_time: now,
    },
    price_context: {
      current_price: 10.5,
      change_pct: 1.2,
      key_levels: { support: 9.7, resistance: 10.4, stop_loss: 9.3 },
    },
    reason: ['量价稳定，持仓观望', '成交额放大', '资金流转正'],
    risk: {
      position_limit: '单票不超过配置上限',
      invalid_if: ['跌破 9.7', '资金流重新转负'],
      notes: ['行情数据可能延迟'],
    },
    valid_until: '2026-07-08T15:00:00+08:00',
    data_time: now,
  },
]

const notifications = [
  {
    notification_id: 'notif-001',
    recommendation_id: 'rec-001',
    symbol: '600000',
    action: 'hold',
    confidence: 'medium',
    key_price: 10.5,
    reason: ['量价稳定，持仓观望'],
    risk: ['跌破 9.7'],
    data_time: now,
    audit_id: 'audit-001',
    status: 'unread',
    created_at: now,
  },
]

const auditLogs = [
  {
    audit_id: 'audit-001',
    event_type: 'recommendation_created',
    recommendation_id: 'rec-001',
    payload: { symbol: '600000' },
    created_at: now,
  },
]

const feedback = [
  {
    feedback_id: 'fb-001',
    recommendation_id: 'rec-001',
    executed: true,
    execution_price: 10.5,
    execution_quantity: 100,
    note: '按计划执行',
    created_at: now,
  },
]

async function setupConsole(
  page: Page,
  options: { workflowReadsAvailable?: boolean } = {},
) {
  const { workflowReadsAvailable = true } = options
  await page.addInitScript(() => {
    localStorage.setItem('qt_console_access_token', 'test-token')
  })

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace('/api/v1', '')

    const responses: Record<string, unknown> = {
      '/service/status': serviceStatus,
      '/positions': positions,
      '/cash/account': cashAccount,
      '/cash/transactions': [],
      '/account/snapshots/latest': accountSnapshot,
      '/watchlist/pinned': watchPinned,
      '/datasource/eastmoney/status': {
        provider: 'eastmoney',
        status: 'missing',
        last_checked_at: null,
        last_error: null,
        updated_at: now,
      },
      '/plans/latest': plan,
      '/recommendations': recommendations,
      '/feedback': feedback,
    }
    if (workflowReadsAvailable) {
      responses['/notifications'] = notifications
      responses['/audit'] = auditLogs
    }

    if (request.method() === 'GET' && path in responses) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(responses[path]),
      })
      return
    }

    await route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ error: { code: 'not_found', message: path, details: {} } }),
    })
  })
}

test('本地控制台框架可渲染并显示安全文案', async ({ page }) => {
  await setupConsole(page)

  await page.goto('/')

  await expect(page.getByRole('heading', { name: '今日仪表盘' })).toBeVisible()
  await expect(page.getByText('只做本地决策辅助，不自动真实下单')).toBeVisible()
  await expect(page.getByRole('heading', { name: '通知摘要' })).toBeVisible()
  await expect(page.getByText('未读: 1')).toBeVisible()
  await expect(page.getByText('待反馈: 1')).toBeVisible()

  const width = page.viewportSize()?.width ?? 0
  if (width < 768) {
    await expect(page.getByRole('link', { name: '移动导航 准备' })).toBeVisible()
    await page.getByRole('link', { name: '移动导航 准备' }).click()
    await expect(page.getByRole('heading', { name: '准备' })).toBeVisible()
  }

  await page.goto('/prepare')
  await expect(page.getByRole('heading', { name: '自选置顶观察池' })).toBeVisible()
  await expect(page.getByText('600519')).toBeVisible()
  await expect(page.getByText('核心自选')).toBeVisible()

  await page.goto('/recommendations')
  await expect(page.getByRole('heading', { name: '建议' })).toBeVisible()
  await page.getByRole('button', { name: '查看详情 600000' }).click()
  await expect(page.getByRole('dialog', { name: '建议详情' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '理由' })).toBeVisible()
  await expect(page.getByText('审计 ID:')).toBeVisible()
  await expect(page.getByText('audit-001')).toBeVisible()

  await page.goto('/review')
  await expect(page.getByRole('heading', { name: '人工执行反馈' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '审计日志' })).toBeVisible()
  await expect(page.getByText('按计划执行')).toBeVisible()
  await expect(page.getByText('audit-001')).toBeVisible()
})

test('本地控制台在通知和审计读取接口不可用时降级展示', async ({ page }) => {
  await setupConsole(page, { workflowReadsAvailable: false })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: '今日仪表盘' })).toBeVisible()
  await expect(page.getByText('通知数据不可用')).toBeVisible()
  await expect(page.getByText('待反馈: 不可用')).toBeVisible()

  await page.goto('/recommendations')
  await page.getByRole('button', { name: '查看详情 600000' }).click()
  await expect(page.getByRole('dialog', { name: '建议详情' })).toBeVisible()
  await expect(page.getByText('审计数据不可用')).toBeVisible()

  await page.goto('/review')
  await expect(page.getByRole('heading', { name: '人工执行反馈' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '审计日志' })).toBeVisible()
  await expect(page.getByText('审计日志数据不可用')).toBeVisible()
  await expect(page.getByText('通知数据不可用')).toBeVisible()
})
