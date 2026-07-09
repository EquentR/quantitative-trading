export type AuthStatus = 'configured' | 'setup_required'
export type SchedulerLastStatus = 'success' | 'failed' | 'running'
export type AccountSnapshotStatus = 'ok' | 'partial' | 'market_data_unavailable' | 'cash_not_initialized'
export type PositionValuationStatus = 'ok' | 'failed' | 'stale'
export type CashTransactionType =
  | 'initial_deposit'
  | 'transfer_in'
  | 'transfer_out'
  | 'cash_adjustment'

export interface LoginResponse {
  access_token: string
  token_type: 'bearer' | string
  expires_at: string
}

export interface ServiceStatus {
  auth_status: AuthStatus
  scheduler_enabled?: boolean
  scheduler_running?: boolean
  interval_seconds?: number
  timezone?: string
  run_on_start?: boolean
  next_run_time?: string | null
  last_started_at?: string | null
  last_finished_at?: string | null
  last_status?: SchedulerLastStatus | null
  last_reason?: string | null
  last_error?: string | null
  last_snapshot_id?: number | null
}

export interface PositionInput {
  symbol: string
  name: string
  quantity: number
  available_quantity: number
  cost_price: number
  opened_at: string
  note: string
}

export interface Position extends PositionInput {
  updated_at: string
}

export interface CashAccount {
  cash_balance: number
  total_transfer_in: number
  total_transfer_out: number
  net_principal: number
  updated_at: string
}

export interface CashTransaction {
  id: number | null
  type: CashTransactionType
  amount: number
  cash_before: number
  cash_after: number
  occurred_at: string
  note: string
}

export interface PositionValuation {
  symbol: string
  name: string
  quantity: number
  available_quantity: number
  cost_price: number
  position_cost: number
  current_price: number | null
  market_value: number | null
  floating_pnl: number | null
  floating_pnl_pct: number | null
  ledger_updated_at: string
  quote_data_time: string | null
  quote_fetched_at: string | null
  status: PositionValuationStatus
  warning: string
}

export interface AccountSnapshot {
  cash_balance: number | null
  net_principal: number | null
  market_value: number | null
  position_cost: number | null
  floating_pnl: number | null
  floating_pnl_pct: number | null
  total_assets: number | null
  total_pnl: number | null
  total_pnl_pct: number | null
  position_ratio: number | null
  available_buying_cash: number | null
  positions: PositionValuation[]
  status: AccountSnapshotStatus
  warnings: string[]
  created_at: string
}

export interface CreatedSnapshotResponse {
  snapshot_id: number
  snapshot: AccountSnapshot
}

export interface ApiErrorPayload {
  error?: {
    code?: string
    message?: string
    details?: unknown
  }
}

export type NotificationProcessingStatus = 'unread' | 'read' | 'feedback_recorded'
export type RecommendationAction = 'buy' | 'sell' | 'add' | 'reduce' | 'hold' | 'watch' | 'avoid'
export type RecommendationConfidence = 'low' | 'medium' | 'high'
export type WatchPinnedSource = 'manual' | 'synced' | 'manual_synced'
export type UniverseSource = 'holding' | 'watch_pinned'
export type DatasourceStatusCode = 'configured' | 'missing' | 'invalid'
export type TradingPlanStatus = 'active' | 'expired' | 'stale'

export interface WatchPinnedInput {
  symbol: string
  name: string
  rank: number
  plan_enabled: boolean
  note: string
}

export interface WatchPinnedItem extends WatchPinnedInput {
  source: WatchPinnedSource
  updated_at: string
}

export interface UniverseMember {
  symbol: string
  name: string
  sources: UniverseSource[]
  priority: number
  ledger_updated_at: string | null
  watch_pinned_rank: number | null
  plan_enabled: boolean
  plan_enabled_source: UniverseSource
  created_at: string
}

export interface UniverseSnapshot {
  created_at: string
  status: 'ok'
  warnings: string[]
  members: UniverseMember[]
}

export interface CreatedUniverseSnapshotResponse {
  snapshot_id: number
  snapshot: UniverseSnapshot
}

export interface DatasourceStatus {
  provider: string
  status: DatasourceStatusCode
  last_checked_at: string | null
  last_error: string | null
  updated_at: string
}

export interface TradingPlan {
  plan_id: string
  trading_day: string
  generated_at: string
  valid_until: string
  universe_snapshot_id: number
  account_snapshot_id: number | null
  ledger_max_updated_at: string | null
  watch_symbols: string[]
  holding_symbols: string[]
  key_levels: Record<string, Record<string, number>>
  candidate_actions: Record<string, string[]>
  invalid_if: Record<string, string[]>
  warnings: string[]
  status: TradingPlanStatus
}

export interface CreatedPlanResponse {
  plan_id: string
  plan: TradingPlan
}

export interface Recommendation {
  recommendation_id: string
  symbol: string
  name: string
  action: RecommendationAction
  confidence: RecommendationConfidence
  position_context: Record<string, unknown>
  account_context: Record<string, unknown>
  price_context: Record<string, unknown>
  reason: string[]
  risk: Record<string, unknown>
  valid_until: string
  data_time: string
}

export interface RecommendationScanResponse {
  count: number
  recommendations: Recommendation[]
}

export interface NotificationSummary {
  notification_id: string
  recommendation_id: string
  symbol: string
  action: string
  confidence: string
  key_price: number | null
  reason: string[]
  risk: string[]
  data_time: string
  audit_id: string
  status: NotificationProcessingStatus
  created_at: string
}

export interface AuditLog {
  audit_id: string
  event_type: string
  recommendation_id: string | null
  payload: Record<string, unknown>
  created_at: string
}

export interface ExecutionFeedbackInput {
  recommendation_id: string
  executed: boolean
  execution_price: number | null
  execution_quantity: number | null
  note: string
}

export interface ExecutionFeedback {
  feedback_id: string
  recommendation_id: string
  executed: boolean
  execution_price: number | null
  execution_quantity: number | null
  note: string
  created_at: string
}
