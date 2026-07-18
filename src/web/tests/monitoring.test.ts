import { render, screen, waitFor, within } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createPinia, setActivePinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { http, HttpResponse } from 'msw'
import { beforeEach, expect, test } from 'vitest'
import MonitoringPage from '@/features/monitoring/MonitoringPage.vue'
import { useSessionStore } from '@/stores/session'
import { server } from '@/test/server'
import { mockServiceStatus, mockAccountSnapshot } from '@/mocks/handlers'

function renderMonitoring() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().setToken('test-token')
  return render(MonitoringPage, { global: { plugins: [pinia, VueQueryPlugin] } })
}

beforeEach(() => {
  localStorage.clear()
})

test('展示调度和盘中工作流控制按钮及安全文案', async () => {
  renderMonitoring()

  expect(screen.getByRole('heading', { name: '监控' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '启动工作流调度' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '停止工作流调度' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '刷新行情数据' })).toBeInTheDocument()
  expect(screen.getByText(/不执行真实交易/)).toBeInTheDocument()
})

test('点击刷新行情后按回填和盘中顺序运行并显示终态', async () => {
  const user = userEvent.setup()
  const calls: string[] = []
  server.use(
    http.post('/api/v1/service/workflows/backfill/run', () => {
      calls.push('backfill')
      return HttpResponse.json({
        task: 'backfill', status: 'success', run_id: 'backfill-monitor', snapshot_id: 1,
        plan_id: null, recommendation_ids: [], warnings: ['回填覆盖存在校验提示'], reused: false,
        ready: null, cleaned_rows: null, mode: null, effective_trade_date: '2026-07-17',
        history_cutoff_date: '2026-07-17', requested_symbol_scope: [], lease_expires_at: null,
      })
    }),
    http.post('/api/v1/service/workflows/intraday/run', () => {
      calls.push('intraday')
      return HttpResponse.json({
        task: 'intraday', status: 'success', run_id: 'intraday-monitor', snapshot_id: 2,
        plan_id: null, recommendation_ids: [], warnings: [], reused: true,
        ready: null, cleaned_rows: null, mode: 'display_only', effective_trade_date: '2026-07-17',
        history_cutoff_date: '2026-07-17', requested_symbol_scope: [], lease_expires_at: null,
      })
    }),
  )
  renderMonitoring()

  await user.click(screen.getByRole('button', { name: '刷新行情数据' }))

  await waitFor(() => expect(calls).toEqual(['backfill', 'intraday']))
  expect(screen.getByText('行情展示已刷新，本次未生成交易建议')).toBeInTheDocument()
  const stages = screen.getByRole('region', { name: '行情刷新阶段详情' })
  expect(within(stages).getByText('backfill-monitor')).toBeInTheDocument()
  expect(within(stages).getByText('intraday-monitor')).toBeInTheDocument()
  expect(within(stages).getByText('回填覆盖存在校验提示')).toBeInTheDocument()
  expect(within(stages).getByText('复用已有运行')).toBeInTheDocument()
})

test('报价分时阶段失败时显示阶段化脱敏错误且不显示成功提示', async () => {
  server.use(
    http.post('/api/v1/service/workflows/intraday/run', () =>
      HttpResponse.json(
        { error: { code: 'workflow_failed', message: 'provider token=secret failed', details: {} } },
        { status: 503 },
      ),
    ),
  )
  const user = userEvent.setup()
  renderMonitoring()

  await user.click(screen.getByRole('button', { name: '刷新行情数据' }))

  await waitFor(() => {
    expect(screen.getByText('日 K 已更新，报价/分时刷新失败')).toBeInTheDocument()
  })
  expect(screen.queryByText('行情与建议已刷新')).not.toBeInTheDocument()
  expect(screen.queryByText(/token=secret/)).not.toBeInTheDocument()
})


test('展示实际调度任务并标记非最近运行任务', async () => {
  server.use(
    http.get('/api/v1/service/status', () =>
      HttpResponse.json({ ...mockServiceStatus, last_task_type: 'close', last_status: 'success' }),
    ),
  )
  renderMonitoring()

  await waitFor(() => expect(screen.getByText('盘中决策')).toBeInTheDocument())
  expect(screen.getByText('收盘就绪')).toBeInTheDocument()
  expect(screen.getByText('分钟清理')).toBeInTheDocument()
  expect(screen.getByText('邮件投递')).toBeInTheDocument()
  expect(screen.getByText(/仅显示全局最近一次任务结果/)).toBeInTheDocument()

  const closePlanRow = screen.getByText('收盘就绪').closest('tr')!
  await waitFor(() => expect(closePlanRow).toHaveTextContent('success'))
  expect(screen.getAllByText('非最近运行')).toHaveLength(3)
})

test('last_task_type 匹配盘中决策时展示对应状态', async () => {
  server.use(
    http.get('/api/v1/service/status', () =>
      HttpResponse.json({ ...mockServiceStatus, last_task_type: 'intraday', last_status: 'success' }),
    ),
  )
  renderMonitoring()
  await waitFor(() => expect(screen.getByText('盘中决策')).toBeInTheDocument())
  await waitFor(() => {
    const row = screen.getByText('盘中决策').closest('tr')!
    expect(row).toHaveTextContent('success')
  })
})

test('展示每轮工作流成本和产物计数', async () => {
  renderMonitoring()

  await waitFor(() => expect(screen.getByText('intraday-20260713-1021')).toBeInTheDocument())
  const row = screen.getByText('intraday-20260713-1021').closest('tr')!
  expect(row).toHaveTextContent('1.3 s')
  expect(row).toHaveTextContent('3 次 / 820 ms')
  expect(row).toHaveTextContent('收 62 / 写 64 / 清 0')
  expect(row).toHaveTextContent('计划 0 / 建议 2 / 通知 2 / 邮件 1')
  expect(row).toHaveTextContent('warning 1 / failed 0')
  expect(row).toHaveTextContent('交易日 2026-07-13')
  expect(row).toHaveTextContent('intraday:2026-07-13:1021')
  expect(row).toHaveTextContent('请求 2 / 完成 2')
  expect(row).toHaveTextContent('quote 完成 2')
  expect(row).toHaveTextContent('minute_bar 完成 1 / 降级 1')
  expect(row).toHaveTextContent('intraday_strength 完成 1 / 陈旧 1')
  expect(row).toHaveTextContent('模式 decision')
  expect(row).toHaveTextContent('有效交易日 2026-07-13')
  expect(row).toHaveTextContent('历史截止 2026-07-10')
  expect(row).toHaveTextContent('范围 600000, 600519')
  expect(row).toHaveTextContent('租约')
  expect(row).toHaveTextContent('结束')
})

test('展示调度跳过、超限累计和最近原因', async () => {
  renderMonitoring()

  await waitFor(() => expect(screen.getByText(/最近原因：manual_api/)).toBeInTheDocument())
  expect(screen.getByText(/并发超限 2 次/)).toBeInTheDocument()
  expect(screen.getByText(/跳过 3 次/)).toBeInTheDocument()
})

test('展示最近错误和数据缺口', async () => {
  server.use(
    http.get('/api/v1/service/status', () =>
      HttpResponse.json({ ...mockServiceStatus, last_error: '行情接口超时' }),
    ),
    http.get('/api/v1/account/snapshots/latest', () =>
      HttpResponse.json({ ...mockAccountSnapshot, status: 'partial', warnings: ['行情数据缺口: 600519 无报价'] }),
    ),
  )
  renderMonitoring()
  await waitFor(() => expect(screen.getByText(/行情接口超时/)).toBeInTheDocument())
  await waitFor(() => expect(screen.getByText(/行情数据缺口/)).toBeInTheDocument())
})
