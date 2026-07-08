import { http, HttpResponse } from 'msw'
import type { AccountSnapshot, CashAccount, CashTransaction, Position, ServiceStatus } from '@/api/types'

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
]
