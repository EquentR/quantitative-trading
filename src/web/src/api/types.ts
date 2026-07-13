export type AuthStatus = 'configured' | 'setup_required'
export type SchedulerLastStatus = 'success' | 'degraded' | 'failed' | 'running' | 'skipped'
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
  last_task_type?: string | null
  last_plan_id?: string | null
  last_recommendation_ids?: string[]
  overrun_count?: number
  skipped_count?: number
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

export interface WorkflowRunResponse {
  task: 'close' | 'intraday' | 'backfill' | 'cleanup'
  status: 'success' | 'degraded' | 'failed'
  run_id: string | null
  snapshot_id: number | null
  plan_id: string | null
  recommendation_ids: string[]
  warnings: string[]
  reused: boolean
  ready: boolean | null
  cleaned_rows: number | null
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

export type MarketQualityStatus =
  | 'complete'
  | 'ok'
  | 'partial'
  | 'degraded'
  | 'stale'
  | 'failed'
  | 'unavailable'
export type IntradayStrengthLabel = 'strong' | 'neutral' | 'weak' | 'unavailable'
export type EmailSecurityMode = 'none' | 'starttls' | 'ssl'
export type EmailDeliveryStatus = 'pending' | 'sending' | 'retry' | 'sent' | 'dead'

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page?: number
  page_size?: number
}

export interface MarketSymbolSummary {
  symbol: string
  name: string
  sources: UniverseSource[]
  current_price: number | null
  change_pct: number | null
  recommendation_action: RecommendationAction | null
  intraday_strength: IntradayStrengthLabel
  plan_status: string | null
  quality_status: MarketQualityStatus
  unread_count: number
  data_time: string | null
  warnings: string[]
}

export interface MarketStrengthComponent {
  key: string
  label: string
  value: number | null
  status: MarketQualityStatus
  direction: -1 | 0 | 1 | null
  reason: string
}

export interface MarketOverview {
  symbol: string
  name: string
  snapshot_id: string | number | null
  status: MarketQualityStatus
  data_time: string | null
  fetched_at: string
  warnings: string[]
  position: {
    quantity: number
    available_quantity: number
    cost_price: number
    floating_pnl_pct: number | null
  } | null
  plan: {
    plan_id: string
    status: string
    allowed_actions: string[]
    invalid_if: string[]
    valid_until: string
  } | null
  recommendation: {
    recommendation_id: string
    action: RecommendationAction
    confidence: RecommendationConfidence
    reason: string[]
    data_time: string
  } | null
  market_structure: {
    support: number | null
    resistance: number | null
    atr14: number | null
    trend: string
    reason: string
  } | null
  intraday_strength: {
    label: IntradayStrengthLabel
    confidence: RecommendationConfidence
    components: MarketStrengthComponent[]
    degraded_reason: string | null
  } | null
  risks: string[]
}

export interface DailyBar {
  trade_date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number
  ma5: number | null
  ma10: number | null
  ma20: number | null
  ma60: number | null
}

export interface DailyBarsResponse {
  symbol: string
  adjustment: 'forward'
  status: MarketQualityStatus
  data_time: string | null
  fetched_at: string
  warnings: string[]
  bars: DailyBar[]
}

export interface MoneyFlowRow {
  trade_date: string
  main_net_amount: number
  main_net_ratio: number
  super_large_net_amount: number
  super_large_net_ratio: number
  large_net_amount: number
  large_net_ratio: number
  medium_net_amount: number
  medium_net_ratio: number
  small_net_amount: number
  small_net_ratio: number
}

export interface MoneyFlowResponse {
  symbol: string
  status: MarketQualityStatus
  data_time: string | null
  fetched_at: string
  warnings: string[]
  rows: MoneyFlowRow[]
}

export interface MinuteBar {
  minute: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number
  vwap: number | null
}

export interface RecommendationMarker {
  time: string
  action: RecommendationAction
  price: number
  recommendation_id: string
}

export interface MinuteBarsResponse {
  symbol: string
  trade_date: string
  status: MarketQualityStatus
  data_time: string | null
  fetched_at: string
  previous_close: number | null
  warnings: string[]
  bars: MinuteBar[]
  recommendation_markers: RecommendationMarker[]
}

export interface IntradayStrengthResponse {
  symbol: string
  status: MarketQualityStatus
  label: IntradayStrengthLabel
  confidence: RecommendationConfidence
  data_time: string | null
  fetched_at: string
  coverage_ratio: number | null
  last_minute: string | null
  degraded_reason: string | null
  rule_version: string
  components: MarketStrengthComponent[]
  warnings: string[]
}

export interface MarketTraceDataset {
  dataset: 'quote' | 'history' | 'money_flow' | 'intraday_strength'
  reference_id: string | number | null
  status: MarketQualityStatus
  source: string
  data_start: string | null
  data_end: string | null
  data_time: string | null
  fetched_at: string | null
  warnings: string[]
}

export interface MarketSnapshotTrace {
  symbol: string
  run_id: string
  snapshot_id: string | number
  plan_id: string | null
  recommendation_id: string | null
  audit_id: string | null
  data_time: string | null
  fetched_at: string
  status: MarketQualityStatus
  warnings: string[]
  thresholds: Record<string, number>
  datasets: MarketTraceDataset[]
}

export interface MarketCaptureRun {
  run_id: string
  workflow_type: 'close' | 'intraday' | 'backfill' | 'cleanup'
  trade_date: string
  period_start: string | null
  period_end: string | null
  idempotency_key: string
  status: 'running' | 'succeeded' | 'degraded' | 'failed'
  started_at: string
  finished_at: string | null
  duration_ms: number | null
  requested_symbols: number
  processed_symbols: number
  provider_calls: number
  provider_duration_ms: number
  rows_received: number
  rows_written: number
  cleaned_rows: number
  plan_count: number
  recommendation_count: number
  notification_count: number
  email_outbox_count: number
  retry_count: number
  warning_count: number
  failure_count: number
  error_summary: string
  dataset_counts: Partial<Record<
    'quote' | 'daily_bar' | 'money_flow' | 'minute_bar' | 'intraday_strength',
    { complete: number; degraded: number; failed: number; stale: number }
  >>
}

export interface EmailNotificationSettings {
  configured: boolean
  host: string
  port: number
  username: string
  sender: string
  recipient: string
  security: EmailSecurityMode
  enabled: boolean
  password_configured: boolean
  updated_at: string | null
}

export interface EmailNotificationSettingsUpdate {
  host: string
  port: number
  username: string
  sender: string
  recipient: string
  security: EmailSecurityMode
  enabled: boolean
  password?: string
}

export interface EmailTestResult {
  status: 'sent'
}

export interface EmailConnectionTestResult {
  status: 'connected'
}

export interface EmailDelivery {
  delivery_id: string
  notification_id: string | null
  dedup_key: string
  recipient: string
  subject: string
  body: string
  payload: Record<string, unknown>
  status: EmailDeliveryStatus
  attempt_count: number
  next_attempt_at: string | null
  lease_expires_at: string | null
  last_error: string
  sent_at: string | null
  created_at: string
  updated_at: string
}
