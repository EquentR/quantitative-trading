import { render, screen, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { http, HttpResponse } from 'msw'
import { beforeEach, expect, test } from 'vitest'
import DashboardPage from '@/features/dashboard/DashboardPage.vue'
import { useSessionStore } from '@/stores/session'
import { mockAccountSnapshot } from '@/mocks/handlers'
import { server } from '@/test/server'

function renderDashboard() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().setToken('test-token')
  return render(DashboardPage, { global: { plugins: [pinia, VueQueryPlugin] } })
}

beforeEach(() => {
  localStorage.clear()
})

test('展示仪表盘区块和总资产', async () => {
  renderDashboard()

  expect(screen.getByRole('heading', { name: '今日仪表盘' })).toBeInTheDocument()
  await waitFor(() => expect(screen.getByText('服务与调度')).toBeInTheDocument())
  expect(screen.getByText('账户估值')).toBeInTheDocument()
  expect(screen.getByText('持仓摘要')).toBeInTheDocument()
  expect(screen.getByText('资金摘要')).toBeInTheDocument()
  await waitFor(() => expect(screen.getByText('¥58,500')).toBeInTheDocument())
})

test('快照状态非 ok 时显示警告', async () => {
  server.use(
    http.get('/api/v1/account/snapshots/latest', () =>
      HttpResponse.json({ ...mockAccountSnapshot, status: 'partial', warnings: ['行情部分不可用'] }),
    ),
  )

  renderDashboard()

  await waitFor(() => expect(screen.getByText(/快照数据不完整/)).toBeInTheDocument())
  expect(screen.getByText('行情部分不可用')).toBeInTheDocument()
})

test('无账户快照时提示生成快照', async () => {
  server.use(
    http.get('/api/v1/account/snapshots/latest', () =>
      HttpResponse.json(
        { error: { code: 'snapshot_not_found', message: 'account snapshot not found', details: {} } },
        { status: 404 },
      ),
    ),
  )

  renderDashboard()

  await waitFor(() => expect(screen.getByText('尚未生成账户快照')).toBeInTheDocument())
})
