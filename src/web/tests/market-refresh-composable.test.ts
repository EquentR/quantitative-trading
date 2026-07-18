import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { render, waitFor } from '@testing-library/vue'
import { defineComponent } from 'vue'
import { beforeEach, expect, test, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  client: {
    post: vi.fn(),
    get: vi.fn(),
  },
}))

vi.mock('@/api/client-provider', () => ({
  useApiClient: () => mocks.client,
}))

import {
  useMarketRefreshCoordinator,
  type MarketRefreshResult,
} from '@/composables/useMarketRefreshCoordinator'

function response(task: 'backfill' | 'intraday', runId: string) {
  return {
    task,
    status: 'success',
    run_id: runId,
    snapshot_id: task === 'intraday' ? 1 : null,
    plan_id: null,
    recommendation_ids: [],
    warnings: [],
    reused: false,
    ready: null,
    cleaned_rows: null,
    mode: task === 'intraday' ? 'display_only' : null,
    effective_trade_date: '2026-07-17',
    history_cutoff_date: '2026-07-17',
    requested_symbol_scope: ['600000'],
    lease_expires_at: null,
  }
}

beforeEach(() => {
  mocks.client.post.mockReset()
  mocks.client.get.mockReset()
})

test('重入时旧 run 不覆盖新 run 的终态状态', async () => {
  let coordinator!: ReturnType<typeof useMarketRefreshCoordinator>
  let releaseFirst!: (value: unknown) => void
  const firstResponse = new Promise((resolve) => {
    releaseFirst = resolve
  })
  mocks.client.post.mockImplementation(async (path: string) => {
    const call = mocks.client.post.mock.calls.length
    if (call === 1) return firstResponse
    if (call === 2) return response('backfill', 'backfill-new')
    if (call === 3) return response('intraday', 'intraday-new')
    return response('intraday', 'intraday-old')
  })
  const Component = defineComponent({
    setup() {
      coordinator = useMarketRefreshCoordinator()
      return () => null
    },
  })
  render(Component, {
    global: {
      plugins: [[VueQueryPlugin, { queryClient: new QueryClient() }]],
    },
  })

  const first = coordinator.run()
  await waitFor(() => expect(mocks.client.post).toHaveBeenCalledTimes(1))
  const second = coordinator.run()
  const secondResult = await second
  releaseFirst(response('backfill', 'backfill-old'))

  await expect(first).rejects.toMatchObject({ name: 'MarketRefreshCancelledError' })
  expect(secondResult.stages.intraday.runId).toBe('intraday-new')
  expect(coordinator.result.value).toEqual(secondResult as MarketRefreshResult)
  expect(coordinator.error.value).toBeNull()
  expect(coordinator.message.value).toBe('行情展示已刷新，本次未生成交易建议')
  expect(coordinator.isPending.value).toBe(false)
  expect(mocks.client.post).toHaveBeenCalledTimes(3)
})
