import { render, screen, waitFor } from '@testing-library/vue'
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

test('展示调度和快照控制按钮及安全文案', async () => {
  renderMonitoring()

  expect(screen.getByRole('heading', { name: '监控' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '启动账户快照调度' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '停止账户快照调度' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '生成一次账户快照' })).toBeInTheDocument()
  expect(screen.getByText(/不执行真实交易/)).toBeInTheDocument()
})

test('点击生成快照后显示已请求提示', async () => {
  const user = userEvent.setup()
  renderMonitoring()

  await user.click(screen.getByRole('button', { name: '生成一次账户快照' }))

  await waitFor(() => expect(screen.getByText('已请求生成账户快照')).toBeInTheDocument())
})


test('展示任务状态行并标记非最近运行任务', async () => {
  server.use(
    http.get('/api/v1/service/status', () =>
      HttpResponse.json({ ...mockServiceStatus, last_task_type: 'close_plan_daily', last_status: 'success' }),
    ),
  )
  renderMonitoring()

  await waitFor(() => expect(screen.getByText('账户快照任务')).toBeInTheDocument())
  expect(screen.getByText('收盘计划任务')).toBeInTheDocument()
  expect(screen.getByText('盘中触发任务')).toBeInTheDocument()
  expect(screen.getByText(/仅显示全局最近一次任务结果/)).toBeInTheDocument()

  const closePlanRow = screen.getByText('收盘计划任务').closest('tr')!
  await waitFor(() => expect(closePlanRow).toHaveTextContent('success'))
  expect(screen.getAllByText('非最近运行')).toHaveLength(2)
})

test('last_task_type 匹配账户快照任务时展示对应状态', async () => {
  server.use(
    http.get('/api/v1/service/status', () =>
      HttpResponse.json({ ...mockServiceStatus, last_task_type: 'account_snapshot', last_status: 'success' }),
    ),
  )
  renderMonitoring()
  await waitFor(() => expect(screen.getByText('账户快照任务')).toBeInTheDocument())
  await waitFor(() => {
    const row = screen.getByText('账户快照任务').closest('tr')!
    expect(row).toHaveTextContent('success')
  })
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
