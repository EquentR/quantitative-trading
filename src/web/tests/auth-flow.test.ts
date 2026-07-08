import { render, screen, waitFor } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createPinia, setActivePinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, expect, test } from 'vitest'
import { http, HttpResponse } from 'msw'
import App from '@/App.vue'
import LoginPage from '@/features/auth/LoginPage.vue'
import SetupPage from '@/features/auth/SetupPage.vue'
import { applyAuthGuard, routes } from '@/router'
import { server } from '@/test/server'
import { useSessionStore } from '@/stores/session'

function providers() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({ history: createMemoryHistory(), routes: [] })
  return { pinia, router }
}

beforeEach(() => {
  localStorage.clear()
})

test('登录成功后保存 token 并显示成功文案', async () => {
  const user = userEvent.setup()
  const { pinia, router } = providers()
  const session = useSessionStore()

  render(LoginPage, { global: { plugins: [pinia, VueQueryPlugin, router] } })

  await user.type(screen.getByLabelText('访问密码'), 'test-password')
  await user.click(screen.getByRole('button', { name: '登录本地控制台' }))

  await waitFor(() => expect(screen.getByText('已登录本地控制台')).toBeInTheDocument())
  expect(session.token).toBe('test-token')
  expect(localStorage.getItem('qt_console_access_token')).toBe('test-token')
})

test('登录页显示不连接真实券商账户说明', () => {
  const { pinia, router } = providers()
  render(LoginPage, { global: { plugins: [pinia, VueQueryPlugin, router] } })

  expect(screen.getByText(/不连接真实券商账户/)).toBeInTheDocument()
})

test('setup 页显示不会保存明文并在成功后提示', async () => {
  const user = userEvent.setup()
  const { pinia, router } = providers()

  render(SetupPage, { global: { plugins: [pinia, VueQueryPlugin, router] } })

  expect(screen.getByText(/不会保存明文/)).toBeInTheDocument()

  await user.type(screen.getByLabelText('设置访问密码'), 'new-password')
  await user.click(screen.getByRole('button', { name: '设置本地访问密码' }))

  await waitFor(() => expect(screen.getByText('访问密码已设置')).toBeInTheDocument())
  expect(screen.getByRole('button', { name: '前往登录' })).toBeInTheDocument()
})

test('登录失败后再次成功仍跳回原始业务路由', async () => {
  const user = userEvent.setup()
  let attempts = 0
  server.use(
    http.post('/api/v1/auth/login', () => {
      attempts += 1
      if (attempts === 1) {
        return HttpResponse.json(
          { error: { code: 'unauthorized', message: 'invalid credentials', details: {} } },
          { status: 401 },
        )
      }
      return HttpResponse.json({
        access_token: 'test-token',
        token_type: 'bearer',
        expires_at: '2026-07-07T18:30:00+08:00',
      })
    }),
  )

  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({ history: createMemoryHistory(), routes })
  applyAuthGuard(router)
  await router.push('/prepare')
  await router.isReady()

  render(App, { global: { plugins: [pinia, VueQueryPlugin, router] } })

  await user.type(screen.getByLabelText('访问密码'), 'wrong-password')
  await user.click(screen.getByRole('button', { name: '登录本地控制台' }))
  await waitFor(() => expect(screen.getByText('登录失败，请检查访问密码。')).toBeInTheDocument())

  await user.click(screen.getByRole('button', { name: '登录本地控制台' }))

  await waitFor(() => expect(router.currentRoute.value.path).toBe('/prepare'))
  expect(screen.getByRole('heading', { name: '准备' })).toBeInTheDocument()
})
