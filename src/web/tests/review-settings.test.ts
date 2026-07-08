import { render, screen, waitFor } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createPinia, setActivePinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, expect, test } from 'vitest'
import ReviewPage from '@/features/review/ReviewPage.vue'
import SettingsPage from '@/features/settings/SettingsPage.vue'
import { useSessionStore } from '@/stores/session'

beforeEach(() => {
  localStorage.clear()
})

test('复盘页显示后续能力说明且不出现买入成功', () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  render(ReviewPage, { global: { plugins: [pinia, VueQueryPlugin] } })

  expect(
    screen.getByText('推荐记录、人工执行反馈、审计日志将在后续 API 落地后接入'),
  ).toBeInTheDocument()
  expect(screen.queryByText('买入成功')).not.toBeInTheDocument()
})

test('设置页保存 API 地址到 localStorage', async () => {
  const user = userEvent.setup()
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().setToken('test-token')
  const router = createRouter({ history: createMemoryHistory(), routes: [] })
  render(SettingsPage, { global: { plugins: [pinia, VueQueryPlugin, router] } })

  await user.clear(screen.getByLabelText('API 地址'))
  await user.type(screen.getByLabelText('API 地址'), 'http://127.0.0.1:9000')
  await user.click(screen.getByRole('button', { name: '保存本地设置' }))

  await waitFor(() =>
    expect(localStorage.getItem('qt_console_api_base_url')).toBe('http://127.0.0.1:9000'),
  )
})

test('设置页退出登录只清除前端 token', async () => {
  const user = userEvent.setup()
  const pinia = createPinia()
  setActivePinia(pinia)
  const session = useSessionStore()
  session.setToken('test-token')
  session.setApiBaseUrl('http://127.0.0.1:9000')
  const router = createRouter({ history: createMemoryHistory(), routes: [] })
  render(SettingsPage, { global: { plugins: [pinia, VueQueryPlugin, router] } })

  await user.click(screen.getByRole('button', { name: '退出登录' }))

  await waitFor(() => expect(session.token).toBeNull())
  expect(localStorage.getItem('qt_console_access_token')).toBeNull()
  expect(session.apiBaseUrl).toBe('http://127.0.0.1:9000')
  expect(screen.getByText(/不保存明文访问密码/)).toBeInTheDocument()
})
