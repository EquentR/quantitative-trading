import { expect, test, type Page, type Route } from '@playwright/test'
import {
  mockCashAccount,
  mockCashTransactions,
  mockDatasourceStatus,
  mockInstrumentPreview,
  mockPositions,
  mockWatchPinned,
} from '../src/mocks/handlers'
import type { InstrumentPreview, WatchPinnedItem } from '../src/api/types'

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

interface CandidateScenario {
  eastmoneyPreview?: InstrumentPreview
  searchPreview?: InstrumentPreview
  selectionError?: { status: number; code: string; message: string }
  selectedWatchlist?: WatchPinnedItem[]
}

interface CandidateRequests {
  eastmoney: number
  searchQueries: string[]
  watchlistReads: number
}

async function setupCandidateConsole(
  page: Page,
  selectionBodies: unknown[],
  scenario: CandidateScenario = {},
) {
  const requests: CandidateRequests = { eastmoney: 0, searchQueries: [], watchlistReads: 0 }
  let watchlistItems = mockWatchPinned
  await page.addInitScript(() => localStorage.setItem('qt_console_access_token', 'test-token'))
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace('/api/v1', '')

    if (request.method() === 'GET' && path === '/positions') return fulfillJson(route, mockPositions)
    if (request.method() === 'GET' && path === '/cash/account') return fulfillJson(route, mockCashAccount)
    if (request.method() === 'GET' && path === '/cash/transactions') return fulfillJson(route, mockCashTransactions)
    if (request.method() === 'GET' && path === '/watchlist/pinned') {
      requests.watchlistReads += 1
      return fulfillJson(route, watchlistItems)
    }
    if (request.method() === 'GET' && path === '/datasource/eastmoney/status') {
      return fulfillJson(route, { ...mockDatasourceStatus, status: 'configured' })
    }
    if (request.method() === 'GET' && path === '/instruments/eastmoney-candidates') {
      requests.eastmoney += 1
      return fulfillJson(route, {
        ...(scenario.eastmoneyPreview ?? mockInstrumentPreview),
        warnings: ['已过滤 1 个不支持的市场品种'],
      })
    }
    if (request.method() === 'GET' && path === '/instruments/search') {
      const query = url.searchParams.get('q') ?? ''
      requests.searchQueries.push(query)
      return fulfillJson(route, scenario.searchPreview ?? {
        ...mockInstrumentPreview,
        source: 'instrument_search',
        query,
        items: mockInstrumentPreview.items.map((item) => ({
          ...item,
          source: 'instrument_search',
          source_rank: null,
        })),
      })
    }
    if (request.method() === 'POST' && path === '/watchlist/pinned/select') {
      selectionBodies.push(request.postDataJSON())
      if (scenario.selectionError) {
        return fulfillJson(route, {
          error: {
            code: scenario.selectionError.code,
            message: scenario.selectionError.message,
            details: {},
          },
        }, scenario.selectionError.status)
      }
      watchlistItems = scenario.selectedWatchlist ?? mockWatchPinned
      return fulfillJson(route, { items: watchlistItems, warnings: [] })
    }

    return fulfillJson(route, { error: { code: 'not_found', message: path, details: {} } }, 404)
  })
  return requests
}

test('东方财富候选在桌面和移动视口中多选后由人工确认加入监控', async ({ page }) => {
  const selectionBodies: unknown[] = []
  const secondCandidate = {
    ...mockInstrumentPreview.items[0],
    symbol: '159915',
    name: '创业板ETF',
    exchange: 'SZ' as const,
    source_rank: 2,
  }
  await setupCandidateConsole(page, selectionBodies, {
    eastmoneyPreview: {
      ...mockInstrumentPreview,
      items: [...mockInstrumentPreview.items, secondCandidate],
    },
  })
  await page.goto('/prepare')

  await expect(page.getByText('JSON/CSV 导入会全量替换当前观察池')).toBeVisible()
  await page.getByRole('button', { name: '从东方财富选择' }).click()
  const warningsToggle = page.getByRole('button', { name: '目录校验提示 1 条' })
  await expect(warningsToggle).toHaveAttribute('aria-expanded', 'false')
  await expect(page.getByText('已过滤 1 个不支持的市场品种')).toHaveCount(0)
  await warningsToggle.click()
  await expect(page.getByText('已过滤 1 个不支持的市场品种')).toBeVisible()
  await expect(page.getByText('510300')).toBeVisible()
  await expect(page.getByText('159915')).toBeVisible()

  await page.getByRole('checkbox', { name: '选择 510300' }).check()
  await page.getByRole('checkbox', { name: '选择 159915' }).check()
  await page.getByRole('button', { name: '加入监控' }).click()

  await expect(page.getByText('已加入 2 个监控标的')).toBeVisible()
  await expect.poll(() => selectionBodies).toEqual([
    {
      preview_id: mockInstrumentPreview.preview_id,
      symbols: ['510300', '159915'],
    },
  ])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
})

test('搜索模式只请求证券目录而不请求东方财富候选', async ({ page }) => {
  const selectionBodies: unknown[] = []
  const requests = await setupCandidateConsole(page, selectionBodies)
  await page.goto('/prepare')

  await page.getByRole('button', { name: '按名称或代码搜索' }).click()
  const searchbox = page.getByRole('searchbox', { name: '股票名称或代码' })
  await searchbox.fill('  510  ')
  await page.getByRole('button', { name: '搜索证券' }).click()

  await expect(page.getByText('510300')).toBeVisible()
  await expect.poll(() => requests.searchQueries).toEqual(['510'])
  expect(requests.eastmoney).toBe(0)
  expect(selectionBodies).toEqual([])
})

test('已监控候选不可重复选择', async ({ page }) => {
  const selectionBodies: unknown[] = []
  const monitoredPreview: InstrumentPreview = {
    ...mockInstrumentPreview,
    items: [{
      ...mockInstrumentPreview.items[0],
      symbol: '600519',
      name: '示例白酒',
      instrument_type: 'a_share',
      already_monitored: true,
    }],
  }
  await setupCandidateConsole(page, selectionBodies, { eastmoneyPreview: monitoredPreview })
  await page.goto('/prepare')

  await page.getByRole('button', { name: '从东方财富选择' }).click()

  await expect(page.getByText('已监控')).toBeVisible()
  await expect(page.getByRole('checkbox', { name: '选择 600519' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '加入监控' })).toBeDisabled()
  expect(selectionBodies).toEqual([])
})

test('确认时预览过期会清空候选并提示重新获取', async ({ page }) => {
  const selectionBodies: unknown[] = []
  await setupCandidateConsole(page, selectionBodies, {
    selectionError: {
      status: 410,
      code: 'instrument_preview_expired',
      message: 'preview expired',
    },
  })
  await page.goto('/prepare')

  await page.getByRole('button', { name: '从东方财富选择' }).click()
  await page.getByRole('checkbox', { name: '选择 510300' }).check()
  await page.getByRole('button', { name: '加入监控' }).click()

  await expect(page.getByText('候选预览已过期，请重新获取')).toBeVisible()
  await expect(page.getByRole('checkbox', { name: '选择 510300' })).toHaveCount(0)
  expect(selectionBodies).toEqual([{
    preview_id: mockInstrumentPreview.preview_id,
    symbols: ['510300'],
  }])
})

test('选择成功后清空候选并刷新观察池查询', async ({ page }) => {
  const selectionBodies: unknown[] = []
  const selectedItem: WatchPinnedItem = {
    ...mockWatchPinned[0],
    symbol: '510300',
    name: '沪深300ETF',
    rank: 2,
    source: 'synced',
    note: '',
    instrument_type: 'etf',
  }
  const requests = await setupCandidateConsole(page, selectionBodies, {
    selectedWatchlist: [...mockWatchPinned, selectedItem],
  })
  await page.goto('/prepare')
  await expect.poll(() => requests.watchlistReads).toBeGreaterThan(0)
  const readsBeforeSelection = requests.watchlistReads

  await page.getByRole('button', { name: '从东方财富选择' }).click()
  await page.getByRole('checkbox', { name: '选择 510300' }).check()
  await page.getByRole('button', { name: '加入监控' }).click()

  await expect(page.getByText('已加入 1 个监控标的')).toBeVisible()
  await expect(page.getByRole('checkbox', { name: '选择 510300' })).toHaveCount(0)
  await expect.poll(() => requests.watchlistReads).toBeGreaterThan(readsBeforeSelection)
  await expect(page.getByRole('checkbox', { name: '计划启用 510300' })).toBeVisible()
})
