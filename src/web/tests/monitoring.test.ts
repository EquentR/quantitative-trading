import { render, screen, waitFor } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createPinia, setActivePinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { beforeEach, expect, test } from 'vitest'
import MonitoringPage from '@/features/monitoring/MonitoringPage.vue'
import { useSessionStore } from '@/stores/session'

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
