import { render, screen, waitFor } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createPinia, setActivePinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { http, HttpResponse } from 'msw'
import { beforeEach, expect, test, vi } from 'vitest'
import PreparationPage from '@/features/preparation/PreparationPage.vue'
import { server } from '@/test/server'
import { useSessionStore } from '@/stores/session'

function renderPreparation() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().setToken('test-token')
  return render(PreparationPage, { global: { plugins: [pinia, VueQueryPlugin] } })
}

beforeEach(() => {
  localStorage.clear()
})

test('展示手动持仓台账和手动资金账户及安全文案', async () => {
  renderPreparation()

  expect(screen.getByRole('heading', { name: '准备' })).toBeInTheDocument()
  await waitFor(() => expect(screen.getByText('手动持仓台账')).toBeInTheDocument())
  expect(screen.getByText('手动资金账户')).toBeInTheDocument()
  expect(screen.getByText('删除台账记录不代表真实卖出或撤单')).toBeInTheDocument()
  expect(screen.getByText('不代表券商资金变化')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '导入 JSON 台账' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '导入 CSV 台账' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '导出 CSV 台账' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '初始化资金账户' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '现金校准' })).toBeInTheDocument()
  expect(screen.getByText('资金流水')).toBeInTheDocument()
})

test('点击新增台账记录后出现保存到手动台账按钮', async () => {
  const user = userEvent.setup()
  renderPreparation()

  await user.click(screen.getByRole('button', { name: '新增台账记录' }))

  await waitFor(() => expect(screen.getByRole('button', { name: '保存到手动台账' })).toBeInTheDocument())
})

test('成本价支持三位小数展示和录入', async () => {
  const user = userEvent.setup()
  server.use(
    http.get('/api/v1/positions', () =>
      HttpResponse.json([
        {
          symbol: '600000',
          name: '示例银行',
          quantity: 1000,
          available_quantity: 1000,
          cost_price: 0.123,
          opened_at: '2026-07-01',
          note: '测试三位小数成本',
          updated_at: '2026-07-07T10:30:00+08:00',
        },
      ]),
    ),
  )

  renderPreparation()

  await waitFor(() => expect(screen.getByText('¥0.123')).toBeInTheDocument())

  await user.click(screen.getByRole('button', { name: '新增台账记录' }))

  expect(screen.getByLabelText('成本价')).toHaveAttribute('step', '0.001')
})

test('现金校准提交前要求人工确认', async () => {
  const user = userEvent.setup()
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  renderPreparation()

  await user.click(screen.getByRole('button', { name: '现金校准' }))
  await user.clear(screen.getByLabelText('校准后现金'))
  await user.type(screen.getByLabelText('校准后现金'), '48000')
  await user.type(screen.getByLabelText('校准备注'), '测试校准')
  await user.click(screen.getByRole('button', { name: '保存现金校准' }))

  expect(confirm).toHaveBeenCalledWith('现金校准只修改手动资金账户，不代表券商资金变化。确认继续？')
})
