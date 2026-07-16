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

const now = '2026-07-07T10:30:00+08:00'

beforeEach(() => {
  localStorage.clear()
})

test('展示数据源设置标题与东方财富/妙想及状态徽标', async () => {
  renderPreparation()

  await waitFor(() => expect(screen.getByText('数据源设置')).toBeInTheDocument())
  expect(screen.getByText('东方财富/妙想')).toBeInTheDocument()
  await waitFor(() => expect(screen.getByText('未配置')).toBeInTheDocument())
})

test('提交 API Key 调用 PUT 并在成功后清除/隐藏 Key', async () => {
  const user = userEvent.setup()
  let captured: unknown = null
  let status: 'missing' | 'configured' = 'missing'
  server.use(
    http.get('/api/v1/datasource/eastmoney/status', () =>
      HttpResponse.json({
        provider: 'eastmoney',
        status,
        last_checked_at: status === 'configured' ? now : null,
        last_error: null,
        updated_at: now,
      }),
    ),
    http.put('/api/v1/datasource/eastmoney/key', async ({ request }) => {
      captured = await request.json()
      status = 'configured'
      return HttpResponse.json({ provider: 'eastmoney', status: 'configured', last_checked_at: now, last_error: null, updated_at: now })
    }),
  )

  renderPreparation()

  await waitFor(() => expect(screen.getByText('数据源设置')).toBeInTheDocument())
  await waitFor(() => expect(screen.getByText('未配置')).toBeInTheDocument())

  const input = screen.getByLabelText('API Key')
  await user.type(input, 'secret-key-123')
  await user.click(screen.getByRole('button', { name: '保存 API Key' }))

  await waitFor(() => {
    expect(captured).toEqual({ api_key: 'secret-key-123' })
  })
  await waitFor(() => expect(screen.getByText('已配置')).toBeInTheDocument())

  // Key must not remain visible in DOM after success.
  expect(screen.queryByDisplayValue('secret-key-123')).not.toBeInTheDocument()
  // Key must not leak into localStorage.
  expect(JSON.stringify(Array.from(Object.entries(localStorage)))).not.toContain('secret-key-123')
})

test('重置 API Key 调用 DELETE 并使用安全确认', async () => {
  const user = userEvent.setup()
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  let deleted = false
  server.use(
    http.delete('/api/v1/datasource/eastmoney/key', () => {
      deleted = true
      return HttpResponse.json({ provider: 'eastmoney', status: 'missing', last_checked_at: null, last_error: null, updated_at: now })
    }),
  )

  renderPreparation()

  const resetBtn = await screen.findByRole('button', { name: '重置 API Key' })
  await user.click(resetBtn)

  await waitFor(() => expect(deleted).toBe(true))
  expect(confirm).toHaveBeenCalled()
})

test('检查连接调用 POST 检查接口', async () => {
  const user = userEvent.setup()
  let checked = false
  server.use(
    http.post('/api/v1/datasource/eastmoney/check', () => {
      checked = true
      return HttpResponse.json({ provider: 'eastmoney', status: 'configured', last_checked_at: now, last_error: null, updated_at: now })
    }),
  )

  renderPreparation()

  const checkBtn = await screen.findByRole('button', { name: '检查连接' })
  await user.click(checkBtn)

  await waitFor(() => expect(checked).toBe(true))
})

test('无效数据源状态显示需要重新配置的提示', async () => {
  server.use(
    http.get('/api/v1/datasource/eastmoney/status', () =>
      HttpResponse.json({
        provider: 'eastmoney', status: 'invalid', last_checked_at: now,
        last_error: 'credential rejected', updated_at: now,
      }),
    ),
  )

  renderPreparation()

  await waitFor(() => expect(screen.getByText('东方财富 API Key 无效，请重新配置。')).toBeInTheDocument())
})

test('检查连接失败显示安全错误且不影响 Key 输入', async () => {
  const user = userEvent.setup()
  server.use(
    http.post('/api/v1/datasource/eastmoney/check', () =>
      HttpResponse.json(
        { error: { code: 'datasource_quota_exceeded', message: 'quota exceeded', details: {} } },
        { status: 429 },
      ),
    ),
  )

  renderPreparation()
  const input = await screen.findByLabelText('API Key')
  await user.type(input, 'retry-key-value')
  await user.click(screen.getByRole('button', { name: '检查连接' }))

  await waitFor(() => expect(screen.getByText('检查失败：调用额度已耗尽，请稍后再试')).toBeInTheDocument())
  expect(input).toHaveValue('retry-key-value')
  expect(JSON.stringify(Array.from(Object.entries(localStorage)))).not.toContain('retry-key-value')
})

test('提交 API Key 失败后展示错误提示并保留输入内容，且不泄露到 localStorage', async () => {
  const user = userEvent.setup()
  server.use(
    http.put('/api/v1/datasource/eastmoney/key', () =>
      HttpResponse.json({ error: { code: 'invalid_api_key', message: 'API Key 无效' } }, { status: 422 }),
    ),
  )

  renderPreparation()

  await waitFor(() => expect(screen.getByText('数据源设置')).toBeInTheDocument())

  const input = screen.getByLabelText('API Key')
  await user.type(input, 'bad-key-999')
  await user.click(screen.getByRole('button', { name: '保存 API Key' }))

  // Visible error alert appears.
  await waitFor(() => expect(screen.getByText(/保存失败/)).toBeInTheDocument())

  // The typed key must remain in the input so the user can retry.
  expect(input).toHaveValue('bad-key-999')

  // The key must not leak into localStorage.
  expect(JSON.stringify(Array.from(Object.entries(localStorage)))).not.toContain('bad-key-999')
})
