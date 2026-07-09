import { http, HttpResponse } from 'msw'
import type {
  AccountSnapshot,
  CashAccount,
  CashTransaction,
  Position,
  ServiceStatus,
  WatchPinnedItem,
  UniverseMember,
  UniverseSnapshot,
  DatasourceStatus,
  TradingPlan,
  Recommendation,
  NotificationSummary,
  AuditLog,
  ExecutionFeedback,
} from '@/api/types'

const now = '2026-07-07T10:30:00+08:00'

export const mockServiceStatus: ServiceStatus = {
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
  last_task_type: 'plan_generation',
  last_plan_id: 'plan-001',
  last_recommendation_ids: ['rec-001'],
}

export const mockPositions: Position[] = [
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

export const mockCashAccount: CashAccount = {
  cash_balance: 48000,
  total_transfer_in: 50000,
  total_transfer_out: 0,
  net_principal: 50000,
  updated_at: now,
}

export const mockCashTransactions: CashTransaction[] = [
  {
    id: 1,
    type: 'initial_deposit',
    amount: 50000,
    cash_before: 0,
    cash_after: 50000,
    occurred_at: now,
    note: '测试初始本金',
  },
]

export const mockAccountSnapshot: AccountSnapshot = {
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
  positions: [
    {
      symbol: '600000',
      name: '示例银行',
      quantity: 1000,
      available_quantity: 1000,
      cost_price: 9.5,
      position_cost: 9500,
      current_price: 10.5,
      market_value: 10500,
      floating_pnl: 1000,
      floating_pnl_pct: 0.1053,
      ledger_updated_at: now,
      quote_data_time: now,
      quote_fetched_at: now,
      status: 'ok',
      warning: '',
    },
  ],
  status: 'ok',
  warnings: [],
  created_at: now,
}

export const mockWatchPinned: WatchPinnedItem[] = [
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

export const mockUniverseMembers: UniverseMember[] = [
  {
    symbol: '600000',
    name: '示例银行',
    sources: ['holding'],
    priority: 0,
    ledger_updated_at: now,
    watch_pinned_rank: null,
    plan_enabled: true,
    plan_enabled_source: 'holding',
    created_at: now,
  },
  {
    symbol: '600519',
    name: '示例白酒',
    sources: ['watch_pinned'],
    priority: 1,
    ledger_updated_at: null,
    watch_pinned_rank: 1,
    plan_enabled: true,
    plan_enabled_source: 'watch_pinned',
    created_at: now,
  },
]

export const mockUniverseSnapshot: UniverseSnapshot = {
  created_at: now,
  status: 'ok',
  warnings: [],
  members: mockUniverseMembers,
}

export const mockDatasourceStatus: DatasourceStatus = {
  provider: 'eastmoney',
  status: 'missing',
  last_checked_at: null,
  last_error: null,
  updated_at: now,
}

export const mockTradingPlan: TradingPlan = {
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

export const mockRecommendations: Recommendation[] = [
  {
    recommendation_id: 'rec-001',
    symbol: '600000',
    name: '示例银行',
    action: 'hold',
    confidence: 'medium',
    position_context: { quantity: 1000 },
    account_context: { cash_balance: 48000 },
    price_context: { current_price: 10.5 },
    reason: ['量价稳定，持仓观望'],
    risk: { invalid_if: ['跌破9.0'], notes: [] },
    valid_until: '2026-07-08T15:00:00+08:00',
    data_time: now,
  },
]

export const mockNotifications: NotificationSummary[] = [
  {
    notification_id: 'notif-001',
    recommendation_id: 'rec-001',
    symbol: '600000',
    action: 'hold',
    confidence: 'medium',
    key_price: null,
    reason: ['量价稳定，持仓观望'],
    risk: ['跌破9.0'],
    data_time: now,
    audit_id: 'audit-001',
    status: 'unread',
    created_at: now,
  },
]

export const mockAuditLog: AuditLog = {
  audit_id: 'audit-001',
  event_type: 'recommendation_created',
  recommendation_id: 'rec-001',
  payload: { symbol: '600000' },
  created_at: now,
}

export const mockExecutionFeedback: ExecutionFeedback[] = [
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
export const handlers = [
  http.get('/api/v1/service/status', () => HttpResponse.json(mockServiceStatus)),
  http.post('/api/v1/service/scheduler/start', () =>
    HttpResponse.json({ ...mockServiceStatus, scheduler_enabled: true, scheduler_running: true }),
  ),
  http.post('/api/v1/service/scheduler/stop', () =>
    HttpResponse.json({ ...mockServiceStatus, scheduler_enabled: false, scheduler_running: false }),
  ),
  http.post('/api/v1/service/run-once', () => HttpResponse.json({ ...mockServiceStatus, last_snapshot_id: 2 })),
  http.post('/api/v1/auth/setup-password', () => HttpResponse.json({ auth_status: 'configured' })),
  http.post('/api/v1/auth/login', () =>
    HttpResponse.json({
      access_token: 'test-token',
      token_type: 'bearer',
      expires_at: '2026-07-07T18:30:00+08:00',
    }),
  ),
  http.post('/api/v1/auth/logout', () => HttpResponse.json({ status: 'ok' })),
  http.get('/api/v1/auth/me', () => HttpResponse.json({ user: 'local' })),
  http.get('/api/v1/positions', () => HttpResponse.json(mockPositions)),
  http.post('/api/v1/positions', async ({ request }) => HttpResponse.json(await request.json(), { status: 201 })),
  http.post('/api/v1/positions/import', () => HttpResponse.json(mockPositions)),
  http.post('/api/v1/positions/import-csv', () => HttpResponse.json(mockPositions)),
  http.get('/api/v1/positions/export-csv', () =>
    new HttpResponse(
      'symbol,name,quantity,available_quantity,cost_price,opened_at,note\n600000,示例银行,1000,1000,9.5,2026-07-01,测试持仓\n',
      { headers: { 'content-type': 'text/csv' } },
    ),
  ),
  http.put('/api/v1/positions/:symbol', async ({ request }) => HttpResponse.json(await request.json())),
  http.delete('/api/v1/positions/:symbol', () => new HttpResponse(null, { status: 204 })),
  http.get('/api/v1/cash/account', () => HttpResponse.json(mockCashAccount)),
  http.post('/api/v1/cash/account', () => HttpResponse.json(mockCashAccount, { status: 201 })),
  http.post('/api/v1/cash/transfers', () => HttpResponse.json(mockCashAccount)),
  http.post('/api/v1/cash/adjustments', () => HttpResponse.json(mockCashAccount)),
  http.get('/api/v1/cash/transactions', () => HttpResponse.json(mockCashTransactions)),
  http.get('/api/v1/account/snapshots/latest', () => HttpResponse.json(mockAccountSnapshot)),
  http.post('/api/v1/account/snapshots', () =>
    HttpResponse.json({ snapshot_id: 2, snapshot: mockAccountSnapshot }, { status: 201 }),
  ),
  http.get('/api/v1/watchlist/pinned', () => HttpResponse.json(mockWatchPinned)),
  http.post('/api/v1/watchlist/pinned', async ({ request }) =>
    HttpResponse.json(await request.json(), { status: 201 }),
  ),
  http.put('/api/v1/watchlist/pinned/:symbol', async ({ request }) =>
    HttpResponse.json(await request.json()),
  ),
  http.delete('/api/v1/watchlist/pinned/:symbol', () => new HttpResponse(null, { status: 204 })),
  http.post('/api/v1/watchlist/pinned/import', () => HttpResponse.json(mockWatchPinned)),
  http.post('/api/v1/watchlist/pinned/import-csv', () => HttpResponse.json(mockWatchPinned)),
  http.get('/api/v1/watchlist/pinned/export-csv', () =>
    new HttpResponse('symbol,name,rank,plan_enabled,note\n600519,示例白酒,1,true,核心自选\n', {
      headers: { 'content-type': 'text/csv' },
    }),
  ),
  http.post('/api/v1/watchlist/pinned/sync', () => HttpResponse.json(mockWatchPinned)),
  http.get('/api/v1/universe', () => HttpResponse.json(mockUniverseMembers)),
  http.post('/api/v1/universe/snapshots', () =>
    HttpResponse.json({ snapshot_id: 2, snapshot: mockUniverseSnapshot }, { status: 201 }),
  ),
  http.get('/api/v1/universe/snapshots/latest', () => HttpResponse.json(mockUniverseSnapshot)),
  http.get('/api/v1/datasource/eastmoney/status', () => HttpResponse.json(mockDatasourceStatus)),
  http.put('/api/v1/datasource/eastmoney/key', () =>
    HttpResponse.json({ ...mockDatasourceStatus, status: 'configured', last_checked_at: now }),
  ),
  http.delete('/api/v1/datasource/eastmoney/key', () => HttpResponse.json(mockDatasourceStatus)),
  http.post('/api/v1/datasource/eastmoney/check', () =>
    HttpResponse.json({ ...mockDatasourceStatus, last_checked_at: now }),
  ),
  http.post('/api/v1/plans', async ({ request }) => {
    const body = await request.json().catch(() => ({}))
    const plan = { ...mockTradingPlan, ...(body as Record<string, unknown>).trading_day ? { trading_day: (body as Record<string, unknown>).trading_day as string } : {} }
    return HttpResponse.json({ plan_id: 'plan-001', plan }, { status: 201 })
  }),
  http.get('/api/v1/plans/latest', () => HttpResponse.json(mockTradingPlan)),
  http.get('/api/v1/plans/:plan_id', () => HttpResponse.json(mockTradingPlan)),
  http.post('/api/v1/recommendations/scan', () =>
    HttpResponse.json({ count: mockRecommendations.length, recommendations: mockRecommendations }),
  ),
  http.get('/api/v1/recommendations', () => HttpResponse.json(mockRecommendations)),
  http.get('/api/v1/recommendations/:recommendation_id', () =>
    HttpResponse.json(mockRecommendations[0]),
  ),
  http.get('/api/v1/notifications', () => HttpResponse.json(mockNotifications)),
  http.get('/api/v1/audit', () => HttpResponse.json([mockAuditLog])),
  http.get('/api/v1/audit/:audit_id', () => HttpResponse.json(mockAuditLog)),
  http.get('/api/v1/feedback', ({ request }) => {
    const url = new URL(request.url)
    const recId = url.searchParams.get('recommendation_id')
    if (recId === '' || recId === 'undefined') {
      return HttpResponse.json(
        { error: { code: 'bad_request', message: 'invalid recommendation_id' } },
        { status: 400 },
      )
    }
    return HttpResponse.json(mockExecutionFeedback)
  }),
  http.post('/api/v1/feedback', async ({ request }) => {
    const body = await request.json()
    return HttpResponse.json(
      { ...(body as Record<string, unknown>), feedback_id: 'fb-001', created_at: now },
      { status: 201 },
    )
  }),
]
