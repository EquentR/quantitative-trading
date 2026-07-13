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
  DailyBarsResponse,
  EmailDelivery,
  EmailNotificationSettings,
  IntradayStrengthResponse,
  MarketOverview,
  MarketCaptureRun,
  MarketSnapshotTrace,
  MarketSymbolSummary,
  MinuteBarsResponse,
  MoneyFlowResponse,
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
  overrun_count: 2,
  skipped_count: 3,
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

export const mockNotifications: NotificationSummary[] = [
  {
    notification_id: 'notif-001',
    recommendation_id: 'rec-001',
    symbol: '600000',
    action: 'hold',
    confidence: 'medium',
    key_price: 10.5,
    reason: ['量价稳定，持仓观望', '成交额放大', '资金流转正'],
    risk: ['跌破 9.7', '资金流重新转负'],
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

export const mockMarketSymbols: MarketSymbolSummary[] = [
  {
    symbol: '600000',
    name: '示例银行',
    sources: ['holding'],
    current_price: 10.12,
    change_pct: 1.8,
    recommendation_action: 'hold',
    intraday_strength: 'strong',
    plan_status: 'active',
    quality_status: 'partial',
    unread_count: 1,
    data_time: '2026-07-13T10:21:00+08:00',
    warnings: ['资金流数据更新较慢'],
  },
  {
    symbol: '600519',
    name: '示例白酒',
    sources: ['watch_pinned'],
    current_price: 1428.5,
    change_pct: -0.4,
    recommendation_action: 'watch',
    intraday_strength: 'neutral',
    plan_status: 'active',
    quality_status: 'complete',
    unread_count: 0,
    data_time: '2026-07-13T10:21:00+08:00',
    warnings: [],
  },
]

export const mockMarketOverview: MarketOverview = {
  symbol: '600000',
  name: '示例银行',
  snapshot_id: 'snapshot-101',
  status: 'partial',
  data_time: '2026-07-13T10:21:00+08:00',
  fetched_at: '2026-07-13T10:22:00+08:00',
  warnings: ['资金流数据更新较慢，当前建议已按后端降级规则处理。'],
  position: {
    quantity: 1000,
    available_quantity: 1000,
    cost_price: 9.5,
    floating_pnl_pct: 6.5,
  },
  plan: {
    plan_id: 'plan-20260713',
    status: 'active',
    allowed_actions: ['hold', 'reduce'],
    invalid_if: ['跌破 9.70 后计划失效'],
    valid_until: '2026-07-13T15:00:00+08:00',
  },
  recommendation: {
    recommendation_id: 'rec-600000-001',
    action: 'hold',
    confidence: 'medium',
    reason: ['价格保持在计划支撑位上方', '分时强弱为 strong'],
    data_time: '2026-07-13T10:21:00+08:00',
  },
  market_structure: {
    support: 9.7,
    resistance: 10.4,
    atr14: 0.21,
    trend: '震荡偏强',
    reason: '收盘特征显示价格保持在主要均线上方',
  },
  intraday_strength: {
    label: 'strong',
    confidence: 'medium',
    degraded_reason: null,
    components: [
      {
        key: 'momentum_15m',
        label: '15 分钟动量',
        value: 0.006,
        status: 'complete',
        direction: 1,
        reason: '15 分钟价格动量为正',
      },
      {
        key: 'vwap_position',
        label: 'VWAP 位置',
        value: 0.003,
        status: 'complete',
        direction: 1,
        reason: '最新价位于后端 VWAP 上方',
      },
    ],
  },
  risks: ['行情和资金流可能存在时间差', '跌破 9.70 后计划失效'],
}

export const mockDailyBars: DailyBarsResponse = {
  symbol: '600000',
  adjustment: 'forward',
  status: 'complete',
  data_time: '2026-07-13T15:00:00+08:00',
  fetched_at: '2026-07-13T15:06:00+08:00',
  warnings: [],
  bars: [
    { trade_date: '2026-07-06', open: 9.68, high: 9.88, low: 9.62, close: 9.82, volume: 321000, amount: 3140000, ma5: 9.7, ma10: 9.61, ma20: 9.52, ma60: 9.41 },
    { trade_date: '2026-07-07', open: 9.81, high: 9.96, low: 9.75, close: 9.91, volume: 356000, amount: 3500000, ma5: 9.76, ma10: 9.65, ma20: 9.55, ma60: 9.43 },
    { trade_date: '2026-07-08', open: 9.9, high: 10.03, low: 9.84, close: 9.98, volume: 382000, amount: 3790000, ma5: 9.82, ma10: 9.7, ma20: 9.58, ma60: 9.45 },
    { trade_date: '2026-07-09', open: 9.97, high: 10.12, low: 9.91, close: 10.08, volume: 411000, amount: 4120000, ma5: 9.9, ma10: 9.76, ma20: 9.62, ma60: 9.47 },
    { trade_date: '2026-07-10', open: 10.06, high: 10.18, low: 10.0, close: 10.1, volume: 436000, amount: 4400000, ma5: 9.98, ma10: 9.81, ma20: 9.66, ma60: 9.49 },
    { trade_date: '2026-07-13', open: 10.08, high: 10.2, low: 10.02, close: 10.12, volume: 462000, amount: 4670000, ma5: 10.04, ma10: 9.87, ma20: 9.7, ma60: 9.51 },
  ],
}

export const mockMoneyFlow: MoneyFlowResponse = {
  symbol: '600000',
  status: 'partial',
  data_time: '2026-07-13T15:00:00+08:00',
  fetched_at: '2026-07-13T15:08:00+08:00',
  warnings: ['最近一个交易日的小单数据暂缺'],
  rows: [
    { trade_date: '2026-07-09', main_net_amount: -820000, main_net_ratio: -2.7, super_large_net_amount: -410000, super_large_net_ratio: -1.35, large_net_amount: -410000, large_net_ratio: -1.35, medium_net_amount: 260000, medium_net_ratio: 0.85, small_net_amount: 560000, small_net_ratio: 1.85 },
    { trade_date: '2026-07-10', main_net_amount: 1260000, main_net_ratio: 4.1, super_large_net_amount: 780000, super_large_net_ratio: 2.5, large_net_amount: 480000, large_net_ratio: 1.6, medium_net_amount: -330000, medium_net_ratio: -1.1, small_net_amount: -930000, small_net_ratio: -3 },
    { trade_date: '2026-07-13', main_net_amount: 1680000, main_net_ratio: 5.2, super_large_net_amount: 920000, super_large_net_ratio: 2.8, large_net_amount: 760000, large_net_ratio: 2.4, medium_net_amount: -460000, medium_net_ratio: -1.4, small_net_amount: -1220000, small_net_ratio: -3.8 },
  ],
}

export const mockMinuteBars: MinuteBarsResponse = {
  symbol: '600000',
  trade_date: '2026-07-13',
  status: 'complete',
  data_time: '2026-07-13T10:21:00+08:00',
  fetched_at: '2026-07-13T10:22:00+08:00',
  previous_close: 10.1,
  warnings: [],
  bars: [
    { minute: '09:33', open: 10.08, high: 10.1, low: 10.06, close: 10.09, volume: 32000, amount: 323000, vwap: 10.08 },
    { minute: '09:48', open: 10.09, high: 10.13, low: 10.08, close: 10.12, volume: 28000, amount: 283000, vwap: 10.09 },
    { minute: '10:03', open: 10.12, high: 10.14, low: 10.1, close: 10.11, volume: 19000, amount: 192000, vwap: 10.1 },
    { minute: '10:18', open: 10.11, high: 10.15, low: 10.1, close: 10.14, volume: 35000, amount: 354000, vwap: 10.11 },
    { minute: '10:21', open: 10.14, high: 10.15, low: 10.11, close: 10.12, volume: 21000, amount: 213000, vwap: 10.11 },
  ],
  recommendation_markers: [
    { time: '10:18', action: 'watch', price: 10.14, recommendation_id: 'rec-600000-001' },
  ],
}

export const mockIntradayStrength: IntradayStrengthResponse = {
  symbol: '600000',
  status: 'complete',
  label: 'strong',
  confidence: 'medium',
  data_time: '2026-07-13T10:21:00+08:00',
  fetched_at: '2026-07-13T10:22:00+08:00',
  coverage_ratio: 1,
  last_minute: '10:21',
  degraded_reason: null,
  rule_version: 'intraday-strength-v1',
  components: mockMarketOverview.intraday_strength!.components,
  warnings: [],
}

export const mockMarketTrace: MarketSnapshotTrace = {
  symbol: '600000',
  run_id: 'run-20260713-001',
  snapshot_id: 'snapshot-101',
  plan_id: 'plan-20260713',
  recommendation_id: 'rec-600000-001',
  audit_id: 'audit-001',
  data_time: '2026-07-13T10:21:00+08:00',
  fetched_at: '2026-07-13T10:22:00+08:00',
  status: 'partial',
  warnings: ['资金流数据延迟一个采集周期'],
  thresholds: { stale_trading_minutes: 6 },
  datasets: [
    { dataset: 'quote', reference_id: 'quote-101', status: 'complete', source: 'akshare', data_start: null, data_end: null, data_time: '2026-07-13T10:21:00+08:00', fetched_at: '2026-07-13T10:22:00+08:00', warnings: [] },
    { dataset: 'history', reference_id: 'daily-101', status: 'complete', source: 'akshare', data_start: '2025-07-08', data_end: '2026-07-13', data_time: '2026-07-13T15:00:00+08:00', fetched_at: '2026-07-13T15:06:00+08:00', warnings: [] },
    { dataset: 'money_flow', reference_id: 'flow-101', status: 'partial', source: 'akshare', data_start: '2026-04-13', data_end: '2026-07-13', data_time: '2026-07-13T15:00:00+08:00', fetched_at: '2026-07-13T15:08:00+08:00', warnings: ['小单数据暂缺'] },
    { dataset: 'intraday_strength', reference_id: 'strength-101', status: 'complete', source: 'derived_backend', data_start: '2026-07-13T09:33:00+08:00', data_end: '2026-07-13T10:21:00+08:00', data_time: '2026-07-13T10:21:00+08:00', fetched_at: '2026-07-13T10:22:00+08:00', warnings: [] },
  ],
}

export const mockMarketRuns: MarketCaptureRun[] = [
  {
    run_id: 'intraday-20260713-1021',
    workflow_type: 'intraday',
    trade_date: '2026-07-13',
    period_start: '2026-07-13T10:21:00+08:00',
    period_end: '2026-07-13T10:24:00+08:00',
    idempotency_key: 'intraday:2026-07-13:1021',
    status: 'degraded',
    started_at: '2026-07-13T10:21:00+08:00',
    finished_at: '2026-07-13T10:21:01.250+08:00',
    duration_ms: 1250,
    requested_symbols: 2,
    processed_symbols: 2,
    provider_calls: 3,
    provider_duration_ms: 820,
    rows_received: 62,
    rows_written: 64,
    cleaned_rows: 0,
    plan_count: 0,
    recommendation_count: 2,
    notification_count: 2,
    email_outbox_count: 1,
    retry_count: 0,
    warning_count: 1,
    failure_count: 0,
    error_summary: '',
    dataset_counts: {
      quote: { complete: 2, degraded: 0, failed: 0, stale: 0 },
      minute_bar: { complete: 1, degraded: 1, failed: 0, stale: 0 },
      intraday_strength: { complete: 1, degraded: 0, failed: 0, stale: 1 },
    },
  },
]

export const mockEmailSettings: EmailNotificationSettings = {
  configured: true,
  host: 'smtp.test.local',
  port: 587,
  username: 'mailer',
  sender: 'alerts@test.local',
  recipient: 'owner@test.local',
  security: 'starttls',
  enabled: true,
  password_configured: true,
  updated_at: '2026-07-13T10:30:00+08:00',
}

export const mockEmailDeliveries: EmailDelivery[] = [
  {
    delivery_id: 'delivery-dead-001',
    notification_id: 'notif-001',
    dedup_key: '2026-07-13:rec-600000-001:owner@test.local',
    recipient: 'owner@test.local',
    subject: '示例建议通知',
    body: '不包含凭据的示例邮件正文',
    payload: { recommendation_id: 'rec-600000-001' },
    status: 'dead',
    attempt_count: 6,
    next_attempt_at: null,
    lease_expires_at: null,
    last_error: '连接超时，凭据已隐藏',
    sent_at: null,
    created_at: '2026-07-13T10:18:00+08:00',
    updated_at: '2026-07-13T10:24:00+08:00',
  },
]

export const handlers = [
  http.get('/api/v1/service/status', () => HttpResponse.json(mockServiceStatus)),
  http.get('/api/v1/market/runs', () => HttpResponse.json({
    items: mockMarketRuns,
    total: mockMarketRuns.length,
    page: 1,
    page_size: 20,
  })),
  http.post('/api/v1/service/scheduler/start', () =>
    HttpResponse.json({ ...mockServiceStatus, scheduler_enabled: true, scheduler_running: true }),
  ),
  http.post('/api/v1/service/scheduler/stop', () =>
    HttpResponse.json({ ...mockServiceStatus, scheduler_enabled: false, scheduler_running: false }),
  ),
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
  http.post('/api/v1/service/workflows/intraday/run', () =>
    HttpResponse.json({
      task: 'intraday',
      status: 'success',
      run_id: 'intraday-mock-run',
      snapshot_id: 1,
      plan_id: null,
      recommendation_ids: mockRecommendations.map((item) => item.recommendation_id),
      warnings: [],
      reused: false,
      ready: null,
      cleaned_rows: null,
    }),
  ),
  http.get('/api/v1/recommendations', () => HttpResponse.json({
    items: mockRecommendations,
    total: mockRecommendations.length,
    page: 1,
    page_size: 20,
  })),
  http.get('/api/v1/recommendations/:recommendation_id', () =>
    HttpResponse.json(mockRecommendations[0]),
  ),
  http.get('/api/v1/notifications', () => HttpResponse.json({
    items: mockNotifications,
    total: mockNotifications.length,
    page: 1,
    page_size: 50,
  })),
  http.get('/api/v1/audit', () => HttpResponse.json({
    items: [mockAuditLog],
    total: 1,
    page: 1,
    page_size: 50,
  })),
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
    return HttpResponse.json({
      items: mockExecutionFeedback,
      total: mockExecutionFeedback.length,
      page: 1,
      page_size: 20,
    })
  }),
  http.post('/api/v1/feedback', async ({ request }) => {
    const body = await request.json()
    return HttpResponse.json(
      { ...(body as Record<string, unknown>), feedback_id: 'fb-001', created_at: now },
      { status: 201 },
    )
  }),
  http.get('/api/v1/market/symbols', () =>
    HttpResponse.json({ items: mockMarketSymbols, total: mockMarketSymbols.length }),
  ),
  http.get('/api/v1/market/symbols/:symbol/overview', ({ params }) =>
    HttpResponse.json({
      ...mockMarketOverview,
      symbol: String(params.symbol),
      name: params.symbol === '600519' ? '示例白酒' : mockMarketOverview.name,
    }),
  ),
  http.get('/api/v1/market/symbols/:symbol/daily-bars', ({ params }) =>
    HttpResponse.json({ ...mockDailyBars, symbol: String(params.symbol) }),
  ),
  http.get('/api/v1/market/symbols/:symbol/money-flow', ({ params }) =>
    HttpResponse.json({ ...mockMoneyFlow, symbol: String(params.symbol) }),
  ),
  http.get('/api/v1/market/symbols/:symbol/minute-bars', ({ params }) =>
    HttpResponse.json({ ...mockMinuteBars, symbol: String(params.symbol) }),
  ),
  http.get('/api/v1/market/symbols/:symbol/intraday-strength/latest', ({ params }) =>
    HttpResponse.json({ ...mockIntradayStrength, symbol: String(params.symbol) }),
  ),
  http.get('/api/v1/market/snapshots/:snapshot_id/trace', ({ params, request }) => {
    const symbol = new URL(request.url).searchParams.get('symbol')
    if (!symbol) {
      return HttpResponse.json(
        { error: { code: 'validation_error', message: 'symbol is required' } },
        { status: 422 },
      )
    }
    return HttpResponse.json({ ...mockMarketTrace, symbol, snapshot_id: String(params.snapshot_id) })
  }),
  http.get('/api/v1/settings/notifications/email', () => HttpResponse.json(mockEmailSettings)),
  http.put('/api/v1/settings/notifications/email', async ({ request }) => {
    const body = (await request.json()) as Partial<EmailNotificationSettings> & { password?: string }
    return HttpResponse.json({
      ...mockEmailSettings,
      ...body,
      password_configured: body.password ? true : mockEmailSettings.password_configured,
      updated_at: '2026-07-13T10:31:00+08:00',
      password: undefined,
    })
  }),
  http.delete('/api/v1/settings/notifications/email/password', () =>
    HttpResponse.json({ ...mockEmailSettings, password_configured: false }),
  ),
  http.post('/api/v1/notifications/email/settings/test-connection', () => HttpResponse.json({ status: 'connected' })),
  http.post('/api/v1/settings/notifications/email/test', () => HttpResponse.json({ status: 'sent' })),
  http.get('/api/v1/notifications/email-deliveries', () =>
    HttpResponse.json({
      items: mockEmailDeliveries,
      total: mockEmailDeliveries.length,
      page: 1,
      page_size: 50,
    }),
  ),
  http.post('/api/v1/notifications/email-deliveries/:delivery_id/retry', ({ params }) =>
    HttpResponse.json({ ...mockEmailDeliveries[0], delivery_id: String(params.delivery_id), status: 'pending' }),
  ),
]
