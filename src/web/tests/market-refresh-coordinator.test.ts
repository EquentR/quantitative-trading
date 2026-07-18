import { describe, expect, test, vi } from 'vitest'
import { ApiError, type ApiClient } from '@/api/client'
import {
  executeMarketRefresh,
  marketRefreshErrorMessage,
  marketRefreshMessage,
} from '@/composables/useMarketRefreshCoordinator'
import type { MarketCaptureRun, WorkflowRunResponse } from '@/api/types'

function response(
  task: 'backfill' | 'intraday',
  status: WorkflowRunResponse['status'],
  runId: string,
): WorkflowRunResponse {
  return {
    task,
    status,
    run_id: runId,
    snapshot_id: task === 'intraday' ? 7 : null,
    plan_id: null,
    recommendation_ids: [],
    warnings: status === 'success' ? [] : [`${task} ${status}`],
    reused: false,
    ready: null,
    cleaned_rows: null,
    mode: task === 'intraday' ? 'display_only' : null,
    effective_trade_date: '2026-07-17',
    history_cutoff_date: '2026-07-17',
    requested_symbol_scope: ['600000'],
    lease_expires_at: '2026-07-18T10:10:00+08:00',
  }
}

function runDetail(
  runId: string,
  status: MarketCaptureRun['status'],
  leaseExpiresAt = '2026-07-18T10:10:00+08:00',
): MarketCaptureRun {
  return {
    run_id: runId,
    workflow_type: runId.startsWith('backfill') ? 'backfill' : 'intraday',
    mode: runId.startsWith('backfill') ? null : 'display_only',
    trade_date: '2026-07-17',
    effective_trade_date: '2026-07-17',
    history_cutoff_date: '2026-07-17',
    period_start: null,
    period_end: null,
    requested_symbol_scope: ['600000'],
    lease_expires_at: leaseExpiresAt,
    idempotency_key: `key:${runId}`,
    status,
    started_at: '2026-07-18T10:00:00+08:00',
    finished_at: status === 'running' ? null : '2026-07-18T10:01:00+08:00',
    duration_ms: status === 'running' ? null : 60_000,
    requested_symbols: 1,
    processed_symbols: status === 'running' ? 0 : 1,
    provider_calls: 1,
    provider_duration_ms: 1,
    rows_received: 1,
    rows_written: 1,
    cleaned_rows: 0,
    plan_count: 0,
    recommendation_count: 0,
    notification_count: 0,
    email_outbox_count: 0,
    retry_count: 0,
    warning_count: 0,
    failure_count: 0,
    error_summary: '',
    dataset_counts: {},
  }
}

describe('market refresh coordinator', () => {
  test('keeps a failed backfill stage and continues to successful intraday', async () => {
    const post = vi.fn(async (path: string) => {
      if (path.endsWith('/backfill/run')) return response('backfill', 'failed', 'backfill-1')
      return response('intraday', 'success', 'intraday-1')
    })
    const client = { post } as unknown as Pick<ApiClient, 'post' | 'get'>

    const result = await executeMarketRefresh(client, { symbols: ['600000'] })

    expect(post.mock.calls).toEqual([
      [
        '/service/workflows/backfill/run',
        { as_of_mode: 'latest_complete', symbols: ['600000'] },
      ],
      [
        '/service/workflows/intraday/run',
        { outside_session_mode: 'display_only', manual_reason: 'market_page_refresh' },
      ],
    ])
    expect(result.overallStatus).toBe('partial')
    expect(result.stages.backfill).toMatchObject({
      workflowType: 'backfill',
      runId: 'backfill-1',
      status: 'failed',
      warnings: ['backfill failed'],
      reused: false,
    })
    expect(result.stages.intraday).toMatchObject({
      workflowType: 'intraday',
      runId: 'intraday-1',
      status: 'succeeded',
      mode: 'display_only',
    })
  })

  test('stops before intraday when backfill transport failure has no trusted run', async () => {
    const post = vi.fn(async () => {
      throw new ApiError({
        code: 'workflow_request_invalid',
        message: 'invalid scope',
        details: {},
        status: 422,
      })
    })
    const client = { post } as unknown as Pick<ApiClient, 'post' | 'get'>

    await expect(executeMarketRefresh(client)).rejects.toMatchObject({
      name: 'MarketRefreshFatalError',
      stage: 'backfill',
      code: 'workflow_request_invalid',
    })
    expect(post).toHaveBeenCalledTimes(1)
  })

  test('follows exact 409 run through transient 404 and normalizes succeeded terminal', async () => {
    const post = vi.fn(async (path: string) => {
      if (path.endsWith('/backfill/run')) {
        throw new ApiError({
          code: 'workflow_in_progress',
          message: 'running',
          details: { run_id: 'backfill-active' },
          status: 409,
        })
      }
      return response('intraday', 'success', 'intraday-1')
    })
    const reads: Array<MarketCaptureRun | ApiError> = [
      new ApiError({ code: 'market_run_not_found', message: 'restarting', details: {}, status: 404 }),
      runDetail('backfill-active', 'running'),
      runDetail('backfill-active', 'succeeded'),
    ]
    const get = vi.fn(async () => {
      const value = reads.shift()!
      if (value instanceof ApiError) throw value
      return value
    })
    const client = { post, get } as unknown as Pick<ApiClient, 'post' | 'get'>

    const result = await executeMarketRefresh(client, {
      now: () => new Date('2026-07-18T10:02:00+08:00'),
      sleep: async () => undefined,
    })

    expect(get).toHaveBeenCalledTimes(3)
    expect(get).toHaveBeenCalledWith('/market/runs/backfill-active')
    expect(result.stages.backfill).toMatchObject({
      runId: 'backfill-active',
      status: 'succeeded',
      reused: true,
    })
    expect(result.overallStatus).toBe('success')
  })

  test('reports still-running without claiming failure when backend lease expires', async () => {
    const post = vi.fn(async () => {
      throw new ApiError({
        code: 'workflow_in_progress',
        message: 'running',
        details: { run_id: 'backfill-active' },
        status: 409,
      })
    })
    const get = vi.fn(async () =>
      runDetail('backfill-active', 'running', '2026-07-18T10:01:00+08:00'))
    const client = { post, get } as unknown as Pick<ApiClient, 'post' | 'get'>

    await expect(executeMarketRefresh(client, {
      now: () => new Date('2026-07-18T10:02:00+08:00'),
      sleep: async () => undefined,
    })).rejects.toMatchObject({
      name: 'MarketRefreshPendingError',
      stage: 'backfill',
      runId: 'backfill-active',
    })
  })

  test('stops on unknown run status as a contract error', async () => {
    const post = vi.fn(async () => {
      throw new ApiError({
        code: 'workflow_in_progress',
        message: 'running',
        details: { run_id: 'backfill-active' },
        status: 409,
      })
    })
    const get = vi.fn(async () =>
      ({ ...runDetail('backfill-active', 'running'), status: 'mystery' }))
    const client = { post, get } as unknown as Pick<ApiClient, 'post' | 'get'>

    await expect(executeMarketRefresh(client, {
      now: () => new Date('2026-07-18T10:00:00+08:00'),
    })).rejects.toMatchObject({
      name: 'MarketRefreshContractError',
      stage: 'backfill',
      runId: 'backfill-active',
    })
  })

  test('rejects an unknown direct response status as a contract error', async () => {
    const post = vi.fn(async () => ({
      ...response('backfill', 'success', 'backfill-unknown'),
      status: 'mystery',
    }))
    const client = { post } as unknown as Pick<ApiClient, 'post' | 'get'>

    await expect(executeMarketRefresh(client)).rejects.toMatchObject({
      name: 'MarketRefreshContractError',
      stage: 'backfill',
      runId: 'backfill-unknown',
    })
    expect(post).toHaveBeenCalledTimes(1)
  })

  test('rejects a direct response for a different workflow task', async () => {
    const post = vi.fn(async () => ({
      ...response('backfill', 'success', 'backfill-wrong-task'),
      task: 'intraday',
    }))
    const client = { post } as unknown as Pick<ApiClient, 'post' | 'get'>

    await expect(executeMarketRefresh(client)).rejects.toMatchObject({
      name: 'MarketRefreshContractError',
      stage: 'backfill',
      runId: 'backfill-wrong-task',
    })
  })

  test('does not start intraday after cancellation during direct backfill POST', async () => {
    const controller = new AbortController()
    const post = vi.fn(async () => {
      controller.abort()
      return response('backfill', 'success', 'backfill-cancelled')
    })
    const client = { post } as unknown as Pick<ApiClient, 'post' | 'get'>

    await expect(executeMarketRefresh(client, { signal: controller.signal }))
      .rejects.toMatchObject({ name: 'MarketRefreshCancelledError', stage: 'backfill' })
    expect(post).toHaveBeenCalledTimes(1)
  })

  test('does not accept terminal detail after cancellation during in-flight GET', async () => {
    const controller = new AbortController()
    const post = vi.fn(async () => {
      throw new ApiError({
        code: 'workflow_in_progress', message: 'running',
        details: { run_id: 'backfill-active' }, status: 409,
      })
    })
    const get = vi.fn(async () => {
      controller.abort()
      return runDetail('backfill-active', 'succeeded')
    })
    const client = { post, get } as unknown as Pick<ApiClient, 'post' | 'get'>

    await expect(executeMarketRefresh(client, { signal: controller.signal }))
      .rejects.toMatchObject({ name: 'MarketRefreshCancelledError', stage: 'backfill' })
    expect(post).toHaveBeenCalledTimes(1)
    expect(get).toHaveBeenCalledTimes(1)
  })

  test('stops repeated restart 404 polling when no backend deadline is available', async () => {
    const post = vi.fn(async () => {
      throw new ApiError({
        code: 'workflow_in_progress',
        message: 'running',
        details: { run_id: 'backfill-restarting' },
        status: 409,
      })
    })
    const get = vi.fn(async () => {
      throw new ApiError({
        code: 'market_run_not_found',
        message: 'restarting',
        details: {},
        status: 404,
      })
    })
    const client = { post, get } as unknown as Pick<ApiClient, 'post' | 'get'>

    await expect(executeMarketRefresh(client, {
      sleep: async () => undefined,
    })).rejects.toMatchObject({
      name: 'MarketRefreshPendingError',
      stage: 'backfill',
      runId: 'backfill-restarting',
    })
    expect(get).toHaveBeenCalledTimes(2)
  })

  test('cancels frontend polling without issuing another run request', async () => {
    const controller = new AbortController()
    const post = vi.fn(async () => {
      throw new ApiError({
        code: 'workflow_in_progress',
        message: 'running',
        details: { run_id: 'backfill-active' },
        status: 409,
      })
    })
    const get = vi.fn(async () => runDetail('backfill-active', 'running'))
    const client = { post, get } as unknown as Pick<ApiClient, 'post' | 'get'>

    await expect(executeMarketRefresh(client, {
      signal: controller.signal,
      now: () => new Date('2026-07-18T10:00:00+08:00'),
      sleep: async () => {
        controller.abort()
      },
    })).rejects.toMatchObject({ name: 'MarketRefreshCancelledError' })
    expect(post).toHaveBeenCalledTimes(1)
  })

  test('retries only missing backfill symbols once after following narrower 409 scope', async () => {
    const post = vi.fn(async (path: string, body?: unknown) => {
      if (path.endsWith('/backfill/run') && post.mock.calls.length === 1) {
        throw new ApiError({
          code: 'workflow_in_progress',
          message: 'running',
          details: { run_id: 'backfill-active' },
          status: 409,
        })
      }
      if (path.endsWith('/backfill/run')) {
        expect(body).toEqual({ as_of_mode: 'latest_complete', symbols: ['000001'] })
        return {
          ...response('backfill', 'success', 'backfill-missing'),
          requested_symbol_scope: ['000001'],
        }
      }
      return {
        ...response('intraday', 'success', 'intraday-1'),
        requested_symbol_scope: ['000001', '600000'],
      }
    })
    const get = vi.fn(async () => runDetail('backfill-active', 'succeeded'))
    const client = { post, get } as unknown as Pick<ApiClient, 'post' | 'get'>

    const result = await executeMarketRefresh(client, {
      symbols: ['600000', '000001'],
      now: () => new Date('2026-07-18T10:00:00+08:00'),
      sleep: async () => undefined,
    })

    expect(post).toHaveBeenCalledTimes(3)
    expect(result.stages.backfill.requestedSymbolScope).toEqual(['000001', '600000'])
    expect(result.stages.backfill.status).toBe('succeeded')
    expect(result.overallStatus).toBe('success')
  })

  test('marks partial after one bounded missing-scope retry remains insufficient', async () => {
    const post = vi.fn(async (path: string) => {
      if (path.endsWith('/backfill/run') && post.mock.calls.length === 1) {
        throw new ApiError({
          code: 'workflow_in_progress',
          message: 'running',
          details: { run_id: 'backfill-active' },
          status: 409,
        })
      }
      if (path.endsWith('/backfill/run')) {
        return {
          ...response('backfill', 'success', 'backfill-missing'),
          requested_symbol_scope: [],
        }
      }
      return {
        ...response('intraday', 'success', 'intraday-1'),
        requested_symbol_scope: ['000001', '600000'],
      }
    })
    const get = vi.fn(async () => runDetail('backfill-active', 'succeeded'))
    const client = { post, get } as unknown as Pick<ApiClient, 'post' | 'get'>

    const result = await executeMarketRefresh(client, {
      symbols: ['600000', '000001'],
      now: () => new Date('2026-07-18T10:00:00+08:00'),
      sleep: async () => undefined,
    })

    expect(post).toHaveBeenCalledTimes(3)
    expect(result.stages.backfill.status).toBe('degraded')
    expect(result.stages.backfill.warnings.join(' ')).toContain('000001')
    expect(result.overallStatus).toBe('partial')
  })

  test('marks partial when intraday scope does not cover the unified symbol pool', async () => {
    const post = vi.fn(async (path: string) => {
      if (path.endsWith('/backfill/run')) {
        return {
          ...response('backfill', 'success', 'backfill-1'),
          requested_symbol_scope: ['000001', '600000'],
        }
      }
      return response('intraday', 'success', 'intraday-1')
    })
    const client = { post, get: vi.fn() } as unknown as Pick<ApiClient, 'post' | 'get'>

    const result = await executeMarketRefresh(client, { symbols: ['000001', '600000'] })

    expect(result.stages.intraday.status).toBe('degraded')
    expect(result.stages.intraday.warnings.join(' ')).toContain('000001')
    expect(result.overallStatus).toBe('partial')
  })

  test('uses default backfill scope to validate intraday coverage', async () => {
    const post = vi.fn(async (path: string) => {
      if (path.endsWith('/backfill/run')) {
        return {
          ...response('backfill', 'success', 'backfill-default-scope'),
          requested_symbol_scope: ['000001', '600000'],
        }
      }
      return response('intraday', 'success', 'intraday-narrow')
    })
    const client = { post, get: vi.fn() } as unknown as Pick<ApiClient, 'post' | 'get'>

    const result = await executeMarketRefresh(client)

    expect(result.stages.intraday.status).toBe('degraded')
    expect(result.stages.intraday.warnings.join(' ')).toContain('000001')
  })

  test('reports three phases and invokes terminal invalidation after business terminals', async () => {
    const phases: string[] = []
    const onTerminal = vi.fn(async () => undefined)
    const post = vi.fn(async (path: string) => {
      if (path.endsWith('/backfill/run')) {
        return response('backfill', 'degraded', 'backfill-1')
      }
      return response('intraday', 'success', 'intraday-1')
    })
    const client = { post, get: vi.fn() } as unknown as Pick<ApiClient, 'post' | 'get'>

    const result = await executeMarketRefresh(client, {
      onPhase: (phase) => phases.push(phase),
      onTerminal,
    })

    expect(phases).toEqual(['backfill', 'intraday', 'refreshing'])
    expect(onTerminal).toHaveBeenCalledOnce()
    expect(result.overallStatus).toBe('partial')
    expect(marketRefreshMessage(result)).toBe(
      '行情展示已刷新，数据部分可用，本次未生成交易建议',
    )
  })

  test('invokes partial invalidation after backfill terminal and intraday fatal', async () => {
    const onPartial = vi.fn(async () => undefined)
    const post = vi.fn(async (path: string) => {
      if (path.endsWith('/backfill/run')) return response('backfill', 'success', 'backfill-written')
      throw new ApiError({
        code: 'provider_disabled', message: 'disabled', details: {}, status: 503,
      })
    })
    const client = { post } as unknown as Pick<ApiClient, 'post' | 'get'>

    await expect(executeMarketRefresh(client, { onPartial } as never)).rejects.toMatchObject({
      name: 'MarketRefreshFatalError', stage: 'intraday',
    })
    expect(onPartial).toHaveBeenCalledOnce()
  })

  test('does not claim K line success when failed backfill precedes intraday fatal', async () => {
    const post = vi.fn(async (path: string) => {
      if (path.endsWith('/backfill/run')) return response('backfill', 'failed', 'backfill-failed')
      throw new ApiError({ code: 'provider_disabled', message: 'disabled', details: {}, status: 503 })
    })
    const client = { post } as unknown as Pick<ApiClient, 'post' | 'get'>

    let caught: Error | null = null
    try {
      await executeMarketRefresh(client)
    } catch (error) {
      caught = error as Error
    }

    expect(marketRefreshErrorMessage(caught)).toBe(
      '行情刷新失败，请稍后重试或在监控页查看运行详情',
    )
  })

  test('uses specific display-only, cache, and failed terminal messages', () => {
    const displayResult = {
      overallStatus: 'success' as const,
      stages: {
        backfill: {
          workflowType: 'backfill' as const,
          runId: 'backfill-1',
          status: 'succeeded' as const,
          warnings: [],
          reused: false,
          mode: null,
          requestedSymbolScope: ['600000'],
          scopeReported: true,
          leaseExpiresAt: null,
        },
        intraday: {
          workflowType: 'intraday' as const,
          runId: 'intraday-1',
          status: 'succeeded' as const,
          warnings: [],
          reused: false,
          mode: 'display_only' as const,
          requestedSymbolScope: ['600000'],
          scopeReported: true,
          leaseExpiresAt: null,
        },
      },
    }
    expect(marketRefreshMessage(displayResult)).toBe(
      '行情展示已刷新，本次未生成交易建议',
    )
    expect(marketRefreshMessage({
      ...displayResult,
      overallStatus: 'partial',
      stages: {
        ...displayResult.stages,
        intraday: {
          ...displayResult.stages.intraday,
          status: 'degraded',
          warnings: ['weekend minute data is stale'],
        },
      },
    })).toBe('行情展示已刷新，数据部分可用，本次未生成交易建议')
    expect(marketRefreshMessage({
      ...displayResult,
      overallStatus: 'partial',
      stages: {
        ...displayResult.stages,
        intraday: {
          ...displayResult.stages.intraday,
          status: 'degraded',
          warnings: ['minute_cache reused'],
        },
      },
    })).toBe('已使用当日缓存，数据部分可用')
    expect(marketRefreshMessage({
      ...displayResult,
      overallStatus: 'failed',
      stages: {
        ...displayResult.stages,
        backfill: { ...displayResult.stages.backfill, status: 'failed' },
        intraday: { ...displayResult.stages.intraday, status: 'failed' },
      },
    })).toBe('行情刷新失败')
  })
})
