import { ApiError, type ApiClient } from '@/api/client'
import { useQueryClient } from '@tanstack/vue-query'
import { computed, onScopeDispose, ref } from 'vue'
import { useApiClient } from '@/api/client-provider'
import type { MarketCaptureRun, WorkflowRunResponse } from '@/api/types'

export interface MarketRefreshOptions {
  symbols?: string[]
  signal?: AbortSignal
  pollIntervalMs?: number
  now?: () => Date
  sleep?: (milliseconds: number) => Promise<void>
  onPhase?: (phase: MarketRefreshPhase) => void
  onTerminal?: (result: MarketRefreshResult) => Promise<void> | void
  onPartial?: (stages: PartialStages) => Promise<void> | void
}

export type MarketRefreshPhase = 'idle' | 'backfill' | 'intraday' | 'refreshing'

export type MarketRefreshStageStatus = 'succeeded' | 'degraded' | 'failed'
export type MarketRefreshOverallStatus = 'success' | 'partial' | 'failed'

export interface MarketRefreshStage {
  workflowType: 'backfill' | 'intraday'
  runId: string
  status: MarketRefreshStageStatus
  warnings: string[]
  reused: boolean
  mode: 'decision' | 'display_only' | null
  requestedSymbolScope: string[]
  scopeReported: boolean
  leaseExpiresAt: string | null
}

export interface MarketRefreshResult {
  overallStatus: MarketRefreshOverallStatus
  stages: {
    backfill: MarketRefreshStage
    intraday: MarketRefreshStage
  }
}

export function marketRefreshMessage(result: MarketRefreshResult): string {
  if (result.overallStatus === 'failed') return '行情刷新失败'
  const warnings = [
    ...result.stages.backfill.warnings,
    ...result.stages.intraday.warnings,
  ]
  if (warnings.some((warning) =>
    warning.includes('minute_cache') || warning.includes('当日缓存'))) {
    return '已使用当日缓存，数据部分可用'
  }
  if (
    result.overallStatus === 'partial'
    && result.stages.intraday.mode === 'display_only'
  ) {
    return '行情展示已刷新，数据部分可用，本次未生成交易建议'
  }
  if (result.overallStatus === 'partial') return '行情数据部分可用'
  if (result.stages.intraday.mode === 'display_only') {
    return '行情展示已刷新，本次未生成交易建议'
  }
  return '行情与建议已刷新'
}

export function marketRefreshPhaseMessage(phase: MarketRefreshPhase): string {
  if (phase === 'backfill') return '正在补齐 K 线'
  if (phase === 'intraday') return '正在获取报价与分时'
  if (phase === 'refreshing') return '正在刷新页面数据'
  return ''
}

export function marketRefreshErrorMessage(error: Error | null): string {
  if (error instanceof MarketRefreshPendingError) {
    return '任务仍在后台运行，请在监控页查看'
  }
  if (error instanceof MarketRefreshCancelledError) return ''
  if (
    (error instanceof MarketRefreshFatalError || error instanceof MarketRefreshRunError)
    && error.stage === 'intraday'
    && error.stages.backfill?.status !== 'failed'
  ) {
    return '日 K 已更新，报价/分时刷新失败'
  }
  return error ? '行情刷新失败，请稍后重试或在监控页查看运行详情' : ''
}

type PartialStages = Partial<MarketRefreshResult['stages']>

export class MarketRefreshFatalError extends Error {
  readonly stage: 'backfill' | 'intraday'
  readonly code: string
  readonly stages: PartialStages
  readonly cause: unknown

  constructor(
    stage: 'backfill' | 'intraday',
    cause: unknown,
    stages: PartialStages,
  ) {
    const code = cause instanceof ApiError ? cause.code : 'market_refresh_transport_error'
    super(cause instanceof Error ? cause.message : 'market refresh transport failed')
    this.name = 'MarketRefreshFatalError'
    this.stage = stage
    this.code = code
    this.stages = stages
    this.cause = cause
  }
}

class MarketRefreshRunError extends Error {
  readonly stage: 'backfill' | 'intraday'
  readonly runId: string
  readonly stages: PartialStages

  constructor(
    name: string,
    message: string,
    stage: 'backfill' | 'intraday',
    runId: string,
    stages: PartialStages,
  ) {
    super(message)
    this.name = name
    this.stage = stage
    this.runId = runId
    this.stages = stages
  }
}

export class MarketRefreshPendingError extends MarketRefreshRunError {
  constructor(stage: 'backfill' | 'intraday', runId: string, stages: PartialStages) {
    super(
      'MarketRefreshPendingError',
      '任务仍未结束，请在监控页查看',
      stage,
      runId,
      stages,
    )
  }
}

export class MarketRefreshContractError extends MarketRefreshRunError {
  constructor(
    stage: 'backfill' | 'intraday',
    runId: string,
    stages: PartialStages,
    message = 'workflow run returned an unknown status',
  ) {
    super('MarketRefreshContractError', message, stage, runId, stages)
  }
}

export class MarketRefreshCancelledError extends MarketRefreshRunError {
  constructor(stage: 'backfill' | 'intraday', runId: string, stages: PartialStages) {
    super('MarketRefreshCancelledError', 'market refresh polling cancelled', stage, runId, stages)
  }
}

function projectStage(
  workflowType: 'backfill' | 'intraday',
  response: WorkflowRunResponse,
): MarketRefreshStage {
  if (!response.run_id) {
    throw new ApiError({
      code: 'workflow_run_identity_missing',
      message: `${workflowType} response did not include a run id`,
      details: {},
      status: 500,
    })
  }
  if (response.task !== workflowType) {
    throw new MarketRefreshContractError(
      workflowType,
      response.run_id,
      {},
      'workflow response task does not match requested stage',
    )
  }
  if (!['success', 'degraded', 'failed'].includes(response.status)) {
    throw new MarketRefreshContractError(
      workflowType,
      response.run_id,
      {},
      'workflow response returned an unknown status',
    )
  }
  const requestedSymbolScope: string[] = Array.isArray(response.requested_symbol_scope)
    ? response.requested_symbol_scope
    : []
  const scopeReported = Array.isArray(response.requested_symbol_scope)
  return {
    workflowType,
    runId: response.run_id,
    status: response.status === 'success' ? 'succeeded' : response.status,
    warnings: response.warnings,
    reused: response.reused,
    mode: response.mode,
    requestedSymbolScope,
    scopeReported,
    leaseExpiresAt: response.lease_expires_at,
  }
}

function overallStatus(stages: MarketRefreshResult['stages']): MarketRefreshOverallStatus {
  const statuses = [stages.backfill.status, stages.intraday.status]
  if (statuses.every((status) => status === 'succeeded')) return 'success'
  if (statuses.every((status) => status === 'failed')) return 'failed'
  return 'partial'
}

function runIdFromConflict(error: ApiError): string | null {
  if (error.status !== 409 || error.code !== 'workflow_in_progress') return null
  if (typeof error.details !== 'object' || error.details === null) return null
  const runId = (error.details as Record<string, unknown>).run_id
  return typeof runId === 'string' && runId ? runId : null
}

function retryDeadline(error: ApiError, now: () => Date): number | null {
  if (typeof error.details !== 'object' || error.details === null) return null
  const leaseExpiresAt = (error.details as Record<string, unknown>).lease_expires_at
  if (typeof leaseExpiresAt === 'string') {
    const lease = Date.parse(leaseExpiresAt)
    if (Number.isFinite(lease)) return lease
  }
  const retryAfter = (error.details as Record<string, unknown>).retry_after
  return typeof retryAfter === 'number' && retryAfter > 0
    ? now().getTime() + retryAfter * 1000
    : null
}

function terminalStage(
  stage: 'backfill' | 'intraday',
  run: MarketCaptureRun,
  stages: PartialStages,
): MarketRefreshStage | null {
  if (run.workflow_type !== stage) {
    throw new MarketRefreshContractError(
      stage,
      run.run_id,
      stages,
      'workflow run type does not match requested stage',
    )
  }
  if (run.status === 'running') return null
  if (!['succeeded', 'degraded', 'failed'].includes(run.status)) {
    throw new MarketRefreshContractError(stage, run.run_id, stages)
  }
  return {
    workflowType: stage,
    runId: run.run_id,
    status: run.status as MarketRefreshStageStatus,
    warnings: run.error_summary ? [run.error_summary] : [],
    reused: true,
    mode: run.mode,
    requestedSymbolScope: run.requested_symbol_scope,
    scopeReported: true,
    leaseExpiresAt: run.lease_expires_at,
  }
}

async function defaultSleep(milliseconds: number): Promise<void> {
  await new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

async function followRun(
  client: Pick<ApiClient, 'get'>,
  stage: 'backfill' | 'intraday',
  runId: string,
  conflict: ApiError,
  stages: PartialStages,
  options: MarketRefreshOptions,
): Promise<MarketRefreshStage> {
  const now = options.now ?? (() => new Date())
  const sleep = options.sleep ?? defaultSleep
  const interval = options.pollIntervalMs ?? 2_000
  let deadline = retryDeadline(conflict, now)
  let missingRunReads = 0

  while (true) {
    if (options.signal?.aborted) {
      throw new MarketRefreshCancelledError(stage, runId, stages)
    }
    let run: MarketCaptureRun
    try {
      run = options.signal
        ? await client.get<MarketCaptureRun>(
          `/market/runs/${encodeURIComponent(runId)}`,
          { signal: options.signal },
        )
        : await client.get<MarketCaptureRun>(`/market/runs/${encodeURIComponent(runId)}`)
    } catch (error) {
      if (options.signal?.aborted) {
        throw new MarketRefreshCancelledError(stage, runId, stages)
      }
      if (error instanceof ApiError && error.status === 404) {
        missingRunReads += 1
        if (
          (deadline !== null && now().getTime() >= deadline)
          || (deadline === null && missingRunReads >= 2)
        ) {
          throw new MarketRefreshPendingError(stage, runId, stages)
        }
        await sleep(interval)
        continue
      }
      throw new MarketRefreshFatalError(stage, error, stages)
    }
    if (options.signal?.aborted) {
      throw new MarketRefreshCancelledError(stage, runId, stages)
    }
    if (run.run_id !== runId) {
      throw new MarketRefreshContractError(
        stage,
        runId,
        stages,
        'workflow detail returned a different run id',
      )
    }
    const terminal = terminalStage(stage, run, stages)
    if (terminal) return terminal

    const lease = run.lease_expires_at === null
      ? Number.NaN
      : Date.parse(run.lease_expires_at)
    if (Number.isFinite(lease)) deadline = lease
    if (deadline === null || !Number.isFinite(deadline)) {
      throw new MarketRefreshContractError(
        stage,
        runId,
        stages,
        'running workflow did not provide lease_expires_at or retry_after',
      )
    }
    if (now().getTime() >= deadline) {
      throw new MarketRefreshPendingError(stage, runId, stages)
    }
    await sleep(interval)
  }
}

async function executeStage(
  client: Pick<ApiClient, 'post' | 'get'>,
  stage: 'backfill' | 'intraday',
  body: Record<string, unknown>,
  stages: PartialStages,
  options: MarketRefreshOptions,
): Promise<MarketRefreshStage> {
  if (options.signal?.aborted) {
    throw new MarketRefreshCancelledError(stage, 'pending', stages)
  }
  try {
    const response = options.signal
      ? await client.post<WorkflowRunResponse>(
        `/service/workflows/${stage}/run`,
        body,
        { signal: options.signal },
      )
      : await client.post<WorkflowRunResponse>(
        `/service/workflows/${stage}/run`,
        body,
      )
    const projected = projectStage(stage, response)
    if (options.signal?.aborted) {
      throw new MarketRefreshCancelledError(stage, projected.runId, stages)
    }
    return projected
  } catch (error) {
    if (options.signal?.aborted && !(error instanceof MarketRefreshCancelledError)) {
      throw new MarketRefreshCancelledError(stage, 'pending', stages)
    }
    if (error instanceof ApiError) {
      const runId = runIdFromConflict(error)
      if (runId) return followRun(client, stage, runId, error, stages, options)
    }
    if (error instanceof MarketRefreshRunError || error instanceof MarketRefreshFatalError) {
      throw error
    }
    throw new MarketRefreshFatalError(stage, error, stages)
  }
}

function canonicalSymbols(symbols: string[] | undefined): string[] {
  return [...new Set(symbols ?? [])].sort()
}

function missingSymbols(expected: string[], actual: string[]): string[] {
  const covered = new Set(actual)
  return expected.filter((symbol) => !covered.has(symbol))
}

function mergeBackfillStages(
  first: MarketRefreshStage,
  retry: MarketRefreshStage,
): MarketRefreshStage {
  const statusRank: Record<MarketRefreshStageStatus, number> = {
    succeeded: 0,
    degraded: 1,
    failed: 2,
  }
  return {
    ...retry,
    status: statusRank[first.status] >= statusRank[retry.status]
      ? first.status
      : retry.status,
    warnings: [...new Set([...first.warnings, ...retry.warnings])],
    reused: first.reused && retry.reused,
    requestedSymbolScope: canonicalSymbols([
      ...first.requestedSymbolScope,
      ...retry.requestedSymbolScope,
    ]),
    scopeReported: first.scopeReported && retry.scopeReported,
  }
}

function markScopeIncomplete(
  stage: MarketRefreshStage,
  missing: string[],
  warningPrefix: string,
): MarketRefreshStage {
  if (missing.length === 0) return stage
  return {
    ...stage,
    status: stage.status === 'failed' ? 'failed' : 'degraded',
    warnings: [
      ...stage.warnings,
      `${warningPrefix}: ${missing.join(', ')}`,
    ],
  }
}

export async function executeMarketRefresh(
  client: Pick<ApiClient, 'post' | 'get'>,
  options: MarketRefreshOptions = {},
): Promise<MarketRefreshResult> {
  const stages: PartialStages = {}
  let expectedSymbols = canonicalSymbols(options.symbols)
  options.onPhase?.('backfill')
  stages.backfill = await executeStage(
    client,
    'backfill',
    {
      as_of_mode: 'latest_complete',
      ...(options.symbols ? { symbols: options.symbols } : {}),
    },
    stages,
    options,
  )
  if (
    expectedSymbols.length > 0
    && !stages.backfill.scopeReported
    && !stages.backfill.reused
  ) {
    stages.backfill.requestedSymbolScope = expectedSymbols
  }
  let missingBackfill = missingSymbols(
    expectedSymbols,
    stages.backfill.requestedSymbolScope,
  )
  if (missingBackfill.length > 0 && stages.backfill.reused) {
    let retry: MarketRefreshStage
    try {
      retry = await executeStage(
        client,
        'backfill',
        { as_of_mode: 'latest_complete', symbols: missingBackfill },
        stages,
        options,
      )
    } catch (error) {
      options.onPhase?.('refreshing')
      await options.onPartial?.(stages)
      throw error
    }
    if (!retry.scopeReported && !retry.reused) {
      retry.requestedSymbolScope = missingBackfill
    }
    stages.backfill = mergeBackfillStages(stages.backfill, retry)
    missingBackfill = missingSymbols(
      expectedSymbols,
      stages.backfill.requestedSymbolScope,
    )
  }
  stages.backfill = markScopeIncomplete(
    stages.backfill,
    missingBackfill,
    '回填范围仍缺少标的',
  )
  if (options.symbols === undefined && stages.backfill.scopeReported) {
    expectedSymbols = canonicalSymbols(stages.backfill.requestedSymbolScope)
  }
  options.onPhase?.('intraday')
  try {
    stages.intraday = await executeStage(
      client,
      'intraday',
      {
        outside_session_mode: 'display_only',
        manual_reason: 'market_page_refresh',
      },
      stages,
      options,
    )
  } catch (error) {
    options.onPhase?.('refreshing')
    await options.onPartial?.(stages)
    throw error
  }
  const missingIntraday = missingSymbols(
    expectedSymbols,
    stages.intraday.requestedSymbolScope,
  )
  stages.intraday = markScopeIncomplete(
    stages.intraday,
    missingIntraday,
    '盘中范围未覆盖统一股票池',
  )

  const completedStages = stages as MarketRefreshResult['stages']
  const result = {
    overallStatus: overallStatus(completedStages),
    stages: completedStages,
  }
  options.onPhase?.('refreshing')
  await options.onTerminal?.(result)
  return result
}

async function invalidateRefreshQueries(
  queryClient: ReturnType<typeof useQueryClient>,
): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['market'] }),
    queryClient.invalidateQueries({ queryKey: ['recommendations'] }),
    queryClient.invalidateQueries({ queryKey: ['notifications'] }),
    queryClient.invalidateQueries({ queryKey: ['audit'] }),
    queryClient.invalidateQueries({ queryKey: ['service'] }),
  ])
}

export function useMarketRefreshCoordinator() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  const phase = ref<MarketRefreshPhase>('idle')
  const result = ref<MarketRefreshResult | null>(null)
  const error = ref<Error | null>(null)
  const message = ref('')
  const isPending = ref(false)
  const hasFailed = computed(() => result.value?.overallStatus === 'failed')
  let controller: AbortController | null = null
  let executionId = 0

  async function run(options: Omit<MarketRefreshOptions, 'signal' | 'onPhase' | 'onTerminal'> = {}) {
    controller?.abort()
    controller = new AbortController()
    const activeController = controller
    const currentExecution = ++executionId
    const isCurrent = () => currentExecution === executionId
    isPending.value = true
    error.value = null
    message.value = ''
    result.value = null
    try {
      const completed = await executeMarketRefresh(client, {
        ...options,
        signal: activeController.signal,
        onPhase: (value) => {
          if (isCurrent()) phase.value = value
        },
        onTerminal: async () => invalidateRefreshQueries(queryClient),
        onPartial: async () => invalidateRefreshQueries(queryClient),
      })
      if (isCurrent()) {
        result.value = completed
        message.value = marketRefreshMessage(completed)
      }
      return completed
    } catch (caught) {
      if (isCurrent()) {
        error.value = caught instanceof Error ? caught : new Error('market refresh failed')
      }
      throw caught
    } finally {
      if (isCurrent()) {
        isPending.value = false
        phase.value = 'idle'
      }
    }
  }

  function cancel() {
    controller?.abort()
  }

  onScopeDispose(cancel)

  return {
    phase,
    result,
    error,
    message,
    isPending,
    hasFailed,
    run,
    cancel,
  }
}
