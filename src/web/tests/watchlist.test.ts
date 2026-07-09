import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
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

function watchPanel() {
  return within(screen.getByText('自选置顶观察池').closest('section')!)
}

beforeEach(() => {
  localStorage.clear()
})

test('展示自选置顶观察池标题与已有观察项', async () => {
  renderPreparation()

  await waitFor(() => expect(screen.getByText('自选置顶观察池')).toBeInTheDocument())
  await waitFor(() => expect(screen.getByText('600519')).toBeInTheDocument())
  expect(screen.getByText('示例白酒')).toBeInTheDocument()
  const panel = watchPanel()
  expect(panel.getByRole('button', { name: '导入自选观察项' })).toBeInTheDocument()
  expect(panel.getByRole('button', { name: '导入 CSV 自选' })).toBeInTheDocument()
  expect(panel.getByRole('button', { name: '导出 CSV 自选' })).toBeInTheDocument()
})

test('新增自选记录提交后发送预期 API 调用', async () => {
  const user = userEvent.setup()
  let captured: unknown = null
  server.use(
    http.post('/api/v1/watchlist/pinned', async ({ request }) => {
      captured = await request.json()
      return HttpResponse.json({ ...(captured as Record<string, unknown>), source: 'manual', updated_at: '2026-07-07T10:30:00+08:00' }, { status: 201 })
    }),
  )

  renderPreparation()
  const panel = watchPanel()

  await user.click(await screen.findByRole('button', { name: '新增自选记录' }))

  await user.type(panel.getByLabelText('股票代码'), '600999')
  await user.type(panel.getByLabelText('股票名称'), '测试自选')
  await user.clear(panel.getByLabelText('排序权重'))
  await user.type(panel.getByLabelText('排序权重'), '5')
  await user.click(panel.getByLabelText('计划启用'))
  await user.type(panel.getByLabelText('备注'), '测试导入')

  await user.click(panel.getByRole('button', { name: '保存自选观察项' }))

  await waitFor(() => {
    expect(captured).toEqual({
      symbol: '600999',
      name: '测试自选',
      rank: 5,
      plan_enabled: true,
      note: '测试导入',
    })
  })
})

test('切换计划启用发送 PUT 并翻转 plan_enabled', async () => {
  const user = userEvent.setup()
  let captured: unknown = null
  server.use(
    http.put('/api/v1/watchlist/pinned/:symbol', async ({ request }) => {
      captured = await request.json()
      return HttpResponse.json({ ...(captured as Record<string, unknown>), source: 'manual', updated_at: '2026-07-07T10:30:00+08:00' })
    }),
  )

  renderPreparation()

  const toggle = await screen.findByRole('checkbox', { name: '计划启用 600519' })
  expect(toggle).toBeChecked()
  await user.click(toggle)

  await waitFor(() => {
    expect(captured).toMatchObject({ symbol: '600519', plan_enabled: false })
  })
})

test('删除本地自选记录按钮文案并调用 DELETE', async () => {
  const user = userEvent.setup()
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  let deleted = false
  server.use(
    http.delete('/api/v1/watchlist/pinned/:symbol', () => {
      deleted = true
      return new HttpResponse(null, { status: 204 })
    }),
  )

  renderPreparation()

  const deleteBtn = await screen.findByRole('button', { name: '删除本地自选记录' })
  expect(deleteBtn).toBeInTheDocument()
  await user.click(deleteBtn)

  await waitFor(() => expect(deleted).toBe(true))
  expect(confirm).toHaveBeenCalled()
})

test('JSON 导入自选观察项发送 { items } 结构', async () => {
  const user = userEvent.setup()
  let captured: unknown = null
  server.use(
    http.post('/api/v1/watchlist/pinned/import', async ({ request }) => {
      captured = await request.json()
      return HttpResponse.json([])
    }),
  )

  renderPreparation()

  await user.click(await screen.findByRole('button', { name: '导入自选观察项' }))

  const textarea = screen.getByLabelText('自选 JSON 内容')
  const json = '[{"symbol":"600888","name":"导入测试","rank":2,"plan_enabled":true,"note":""}]'
  await fireEvent.update(textarea, json)
  await user.click(screen.getByRole('button', { name: '保存导入自选' }))

  await waitFor(() => {
    expect(captured).toEqual({
      items: [{ symbol: '600888', name: '导入测试', rank: 2, plan_enabled: true, note: '' }],
    })
  })
})
