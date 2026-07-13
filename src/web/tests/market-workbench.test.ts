import { render, screen, waitFor, within } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createPinia, setActivePinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'
import { http, HttpResponse, delay } from 'msw'
import { beforeEach, expect, test, vi } from 'vitest'
import MarketPage from '@/features/market/MarketPage.vue'
import { server } from '@/test/server'
import { mockDailyBars, mockMarketOverview, mockMarketSymbols, mockMarketTrace, mockMoneyFlow } from '@/mocks/handlers'
import { useSessionStore } from '@/stores/session'

const { setOptionSpy } = vi.hoisted(() => ({ setOptionSpy: vi.fn() }))

vi.mock('echarts/core', () => ({
  init: () => ({ setOption: setOptionSpy, resize: vi.fn(), dispose: vi.fn() }),
  use: vi.fn(),
}))

beforeEach(() => {
  localStorage.clear()
  setOptionSpy.mockClear()
})

async function renderMarket(path = '/market') {
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().setToken('test-token')
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/market', component: MarketPage },
      { path: '/review', component: { template: '<div>复盘目标</div>' } },
      { path: '/recommendations', component: { template: '<div>建议目标</div>' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  return { ...render(MarketPage, { global: { plugins: [pinia, VueQueryPlugin, router] } }), router }
}

test('行情工作台展示决策扫描器、五个标签和概览质量状态', async () => {
  await renderMarket()

  expect(await screen.findByRole('heading', { name: '行情' })).toBeInTheDocument()
  expect(await screen.findByRole('button', { name: /600000 示例银行/ })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /600519 示例白酒/ })).toBeInTheDocument()
  expect(screen.queryByText('000001 未启用标的')).not.toBeInTheDocument()

  for (const tab of ['概览', 'K 线', '资金流', '分时强弱', '数据引用']) {
    expect(screen.getByRole('tab', { name: tab })).toBeInTheDocument()
  }
  expect(await screen.findByText('市场结构')).toBeInTheDocument()
  expect(screen.getByText('数据部分可用')).toBeInTheDocument()
  expect(screen.getByText('跌破 9.70 后计划失效')).toBeInTheDocument()
})

test('扫描器仅在后端股票池内按动作、来源和异常状态筛选', async () => {
  const user = userEvent.setup()
  await renderMarket()

  await screen.findByRole('button', { name: /600000 示例银行/ })
  await user.selectOptions(screen.getByLabelText('动作筛选'), 'watch')
  expect(screen.queryByRole('button', { name: /600000 示例银行/ })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: /600519 示例白酒/ })).toBeInTheDocument()

  await user.selectOptions(screen.getByLabelText('动作筛选'), 'all')
  await user.selectOptions(screen.getByLabelText('来源筛选'), 'holding')
  expect(screen.getByRole('button', { name: /600000 示例银行/ })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /600519 示例白酒/ })).not.toBeInTheDocument()

  await user.selectOptions(screen.getByLabelText('来源筛选'), 'all')
  await user.click(screen.getByLabelText('仅看异常或未读'))
  expect(screen.getByRole('button', { name: /600000 示例银行/ })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /600519 示例白酒/ })).not.toBeInTheDocument()
})

test('扫描器按建议优先、涨跌幅和代码执行稳定排序', async () => {
  const user = userEvent.setup()
  server.use(
    http.get('/api/v1/market/symbols', () => HttpResponse.json({
      items: [
        { ...mockMarketSymbols[0], symbol: '600000', recommendation_action: 'watch', change_pct: -1 },
        { ...mockMarketSymbols[0], symbol: '300001', recommendation_action: 'sell', change_pct: 5 },
        { ...mockMarketSymbols[0], symbol: '000001', recommendation_action: 'buy', change_pct: 5 },
      ],
      total: 3,
    })),
  )
  await renderMarket()

  const scanner = await screen.findByLabelText('决策标的扫描器')
  const symbolsInOrder = () => within(scanner).getAllByRole('button').map((button) =>
    button.textContent?.match(/\d{6}/)?.[0],
  )

  await waitFor(() => expect(symbolsInOrder()).toEqual(['300001', '000001', '600000']))
  await user.selectOptions(within(scanner).getByLabelText('排序方式'), 'change_desc')
  expect(symbolsInOrder()).toEqual(['300001', '000001', '600000'])
  await user.selectOptions(within(scanner).getByLabelText('排序方式'), 'symbol')
  expect(symbolsInOrder()).toEqual(['000001', '300001', '600000'])
})

test('行情页消费 symbol query 并通过 router 导航到对应计划', async () => {
  const user = userEvent.setup()
  const { router } = await renderMarket('/market?symbol=600519')

  expect(await screen.findByRole('heading', { name: '示例白酒' })).toBeInTheDocument()
  await user.click(await screen.findByRole('link', { name: 'plan-20260713' }))
  expect(router.currentRoute.value.path).toBe('/review')
  expect(router.currentRoute.value.query.plan_id).toBe('plan-20260713')
})

test('行情百分点字段直接展示后端百分点值而不重复乘以 100', async () => {
  const user = userEvent.setup()
  server.use(
    http.get('/api/v1/market/symbols', () => HttpResponse.json({
      items: [
        { ...mockMarketSymbols[0], change_pct: 1.8 },
        ...mockMarketSymbols.slice(1),
      ],
      total: mockMarketSymbols.length,
    })),
    http.get('/api/v1/market/symbols/600000/overview', () => HttpResponse.json({
      ...mockMarketOverview,
      position: { ...mockMarketOverview.position!, floating_pnl_pct: 6.5 },
    })),
    http.get('/api/v1/market/symbols/600000/money-flow', () => HttpResponse.json({
      ...mockMoneyFlow,
      rows: mockMoneyFlow.rows.map((row, index) => index === 0
        ? { ...row, main_net_ratio: 5.2 }
        : row),
    })),
  )
  await renderMarket()

  const symbolButton = await screen.findByRole('button', { name: /600000 示例银行/ })
  expect(within(symbolButton).getByText(/1\.80%/)).toBeInTheDocument()
  expect(await screen.findByText('6.50%')).toBeInTheDocument()

  await user.click(screen.getByRole('tab', { name: '资金流' }))
  const moneyTable = await screen.findByRole('table', { name: '资金流完整明细' })
  expect(within(moneyTable).getAllByText('5.20%')).not.toHaveLength(0)
  await waitFor(() => {
    const moneyOption = setOptionSpy.mock.calls
      .map(([option]) => option)
      .find((option) => option.series?.some((series: { name?: string }) => series.name === '主力净占比'))
    expect(moneyOption.yAxis[1].axisLabel.formatter(5.2)).toBe('5%')
  })
})

test('K线、资金流、分时图和数据引用标签读取后端事实并保持稳定图表容器', async () => {
  const user = userEvent.setup()
  let traceSymbol = ''
  server.use(
    http.get('/api/v1/market/snapshots/:snapshot_id/trace', ({ request }) => {
      traceSymbol = new URL(request.url).searchParams.get('symbol') ?? ''
      return HttpResponse.json(mockMarketTrace)
    }),
  )
  await renderMarket()

  await screen.findByRole('tab', { name: 'K 线' })
  await user.click(screen.getByRole('tab', { name: 'K 线' }))
  expect(await screen.findByRole('img', { name: '前复权日 K 线、均线与成交量图' })).toHaveClass('market-chart')
  expect(screen.getByText('前复权')).toBeInTheDocument()

  await user.click(screen.getByRole('tab', { name: '资金流' }))
  expect(await screen.findByRole('img', { name: '资金流净额与占比图' })).toHaveClass('market-chart')
  const moneyTable = screen.getByRole('table', { name: '资金流完整明细' })
  expect(moneyTable).toBeInTheDocument()
  for (const heading of ['超大单净额', '超大单占比', '大单净额', '大单占比', '中单净额', '中单占比', '小单净额', '小单占比']) {
    expect(within(moneyTable).getByText(heading)).toBeInTheDocument()
  }
  expect(within(moneyTable).getByText('2.80%')).toBeInTheDocument()

  await user.click(screen.getByRole('tab', { name: '分时强弱' }))
  expect(await screen.findByRole('img', { name: '分时价格、VWAP 与成交量图' })).toHaveClass('market-chart')
  expect(screen.getByText('VWAP')).toBeInTheDocument()
  expect(screen.getByText('建议发生点 10:18 watch')).toBeInTheDocument()

  await user.click(screen.getByRole('tab', { name: '数据引用' }))
  expect(await screen.findByText('run-20260713-001')).toBeInTheDocument()
  expect(screen.getByText('snapshot-101')).toBeInTheDocument()
  expect(screen.getByText('plan-20260713')).toBeInTheDocument()
  expect(screen.getByText('rec-600000-001')).toBeInTheDocument()
  expect(traceSymbol).toBe('600000')
})

test('移动端标的选择抽屉可打开、切换标的并关闭', async () => {
  const user = userEvent.setup()
  await renderMarket()

  await screen.findByRole('heading', { name: '示例银行' })
  await user.click(screen.getByRole('button', { name: '选择决策标的' }))
  const drawer = screen.getByRole('dialog', { name: '决策标的扫描器' })
  await user.click(within(drawer).getByRole('button', { name: /600519 示例白酒/ }))

  expect(await screen.findByRole('heading', { name: '示例白酒' })).toBeInTheDocument()
  expect(screen.queryByRole('dialog', { name: '决策标的扫描器' })).not.toBeInTheDocument()
})

test('行情页面覆盖加载、空数据和失败状态', async () => {
  server.use(
    http.get('/api/v1/market/symbols', async () => {
      await delay('infinite')
      return HttpResponse.json({ items: [], total: 0 })
    }),
  )
  const loading = await renderMarket()
  expect(screen.getByText('正在加载决策标的')).toBeInTheDocument()
  loading.unmount()

  server.use(
    http.get('/api/v1/market/symbols', () => HttpResponse.json({ items: [], total: 0 })),
  )
  const empty = await renderMarket()
  expect(await screen.findByText('当前没有决策启用标的')).toBeInTheDocument()
  empty.unmount()

  server.use(
    http.get('/api/v1/market/symbols', () =>
      HttpResponse.json(
        { error: { code: 'market_symbols_unavailable', message: 'market symbols unavailable' } },
        { status: 503 },
      ),
    ),
  )
  await renderMarket()
  expect(await screen.findByText('决策标的加载失败')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '重试加载标的' })).toBeInTheDocument()
})

test('stale 标的和长 warning 明确展示且不冒充新数据', async () => {
  server.use(
    http.get('/api/v1/market/symbols/600000/overview', () =>
      HttpResponse.json({
        symbol: '600000',
        name: '一个非常长但必须保持在容器中的示例银行名称',
        status: 'stale',
        data_time: '2026-07-13T09:33:00+08:00',
        fetched_at: '2026-07-13T10:30:00+08:00',
        warnings: ['行情已经超过六个有效交易分钟未更新，需要人工检查数据源而不能把当前系统时间当作行情时间。'],
        position: null,
        plan: null,
        recommendation: null,
        market_structure: null,
        intraday_strength: null,
        risks: [],
      }),
    ),
  )
  await renderMarket()

  expect(await screen.findByText('数据已过期')).toBeInTheDocument()
  expect(screen.getByText(/超过六个有效交易分钟/)).toHaveClass('break-words')
  expect(screen.getByText(/2026\/7\/13 09:33/)).toBeInTheDocument()
})

test('stale 时间序列在图表内部显示陈旧数据标记', async () => {
  const user = userEvent.setup()
  server.use(
    http.get('/api/v1/market/symbols/600000/daily-bars', () =>
      HttpResponse.json({
        ...mockDailyBars,
        status: 'stale',
        data_time: '2026-07-13T14:48:00+08:00',
        warnings: ['日 K 数据已超过允许时效'],
      }),
    ),
  )
  await renderMarket()

  await user.click(await screen.findByRole('tab', { name: 'K 线' }))
  const chart = await screen.findByRole('img', { name: '前复权日 K 线、均线与成交量图' })
  expect(within(chart).getByText(/陈旧数据.*2026-07-13T14:48:00\+08:00/)).toBeInTheDocument()
})
