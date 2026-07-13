import { render, screen, waitFor, within } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createPinia, setActivePinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'
import { http, HttpResponse } from 'msw'
import { beforeEach, expect, test } from 'vitest'
import SettingsPage from '@/features/settings/SettingsPage.vue'
import { server } from '@/test/server'
import { useSessionStore } from '@/stores/session'

beforeEach(() => localStorage.clear())

const smtpSettings = {
  configured: true,
  host: 'smtp.test.local',
  port: 587,
  username: 'mailer',
  sender: 'alerts@test.local',
  recipient: 'owner@test.local',
  security: 'starttls',
  enabled: true,
  password_configured: true,
  updated_at: '2026-07-13T10:30:00+08:00',
}

const failedDelivery = {
  delivery_id: 'delivery-dead-001',
  notification_id: 'notif-001',
  dedup_key: '2026-07-13:rec-600000-001:owner@test.local',
  recipient: 'owner@test.local',
  subject: '示例建议通知',
  body: '不包含凭据的示例邮件正文',
  payload: { recommendation_id: 'rec-600000-001' },
  status: 'dead',
  attempt_count: 6,
  next_attempt_at: null,
  lease_expires_at: null,
  last_error: '连接超时，凭据已隐藏',
  sent_at: null,
  created_at: '2026-07-13T10:18:00+08:00',
  updated_at: '2026-07-13T10:24:00+08:00',
}

function renderSettings() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().setToken('test-token')
  const router = createRouter({ history: createMemoryHistory(), routes: [] })
  return render(SettingsPage, { global: { plugins: [pinia, VueQueryPlugin, router] } })
}

test('SMTP 设置显示明文入库风险、脱敏状态且永不回填密码', async () => {
  server.use(
    http.get('/api/v1/settings/notifications/email', () => HttpResponse.json(smtpSettings)),
  )
  renderSettings()

  expect(await screen.findByRole('heading', { name: '邮件通知' })).toBeInTheDocument()
  expect(screen.getByText(/数据库导出和备份会包含 SMTP 明文密码/)).toBeInTheDocument()
  expect(screen.getByText('密码已配置')).toBeInTheDocument()
  expect(screen.getByLabelText('SMTP 密码')).toHaveValue('')
  expect(screen.queryByDisplayValue('existing-smtp-password')).not.toBeInTheDocument()
  expect(screen.getByText('邮件通道已启用')).toBeInTheDocument()
})

test('保存 SMTP 配置可显式替换密码，留空不会发送 password 字段', async () => {
  const user = userEvent.setup()
  const requests: Record<string, unknown>[] = []
  server.use(
    http.put('/api/v1/settings/notifications/email', async ({ request }) => {
      requests.push((await request.json()) as Record<string, unknown>)
      return HttpResponse.json({
        host: 'smtp.updated.test', port: 587, username: 'mailer', sender: 'alerts@test.local',
        configured: true, recipient: 'owner@test.local', security: 'starttls', enabled: true,
        password_configured: true, updated_at: '2026-07-13T10:30:00+08:00',
      })
    }),
  )
  renderSettings()
  await screen.findByLabelText('SMTP 主机')

  await user.clear(screen.getByLabelText('SMTP 主机'))
  await user.type(screen.getByLabelText('SMTP 主机'), 'smtp.updated.test')
  await user.click(screen.getByRole('button', { name: '保存邮件配置' }))
  await waitFor(() => expect(requests).toHaveLength(1))
  expect(requests[0]).not.toHaveProperty('password')
  expect(requests[0]).toMatchObject({ recipient: 'owner@test.local', security: 'starttls' })
  expect(requests[0]).not.toHaveProperty('recipients')
  expect(requests[0]).not.toHaveProperty('security_mode')

  await user.type(screen.getByLabelText('SMTP 密码'), 'replacement-password')
  await user.click(screen.getByRole('button', { name: '保存邮件配置' }))
  await waitFor(() => expect(requests).toHaveLength(2))
  expect(requests[1]).toMatchObject({ password: 'replacement-password' })
  expect(screen.getByLabelText('SMTP 密码')).toHaveValue('')
})

test('可明确清除密码并分别执行连接测试和测试邮件', async () => {
  const user = userEvent.setup()
  let cleared = false
  const connectionBodies: string[] = []
  const testEmailBodies: string[] = []
  server.use(
    http.delete('/api/v1/settings/notifications/email/password', () => {
      cleared = true
      return HttpResponse.json({ ...smtpSettings, password_configured: false })
    }),
    http.post('/api/v1/notifications/email/settings/test-connection', async ({ request }) => {
      connectionBodies.push(await request.text())
      return HttpResponse.json({ status: 'connected' })
    }),
    http.post('/api/v1/settings/notifications/email/test', async ({ request }) => {
      testEmailBodies.push(await request.text())
      return HttpResponse.json({ status: 'sent' })
    }),
  )
  renderSettings()
  await screen.findByRole('button', { name: '清除 SMTP 密码' })

  await user.click(screen.getByRole('button', { name: '测试 SMTP 连接' }))
  expect(await screen.findByText('连接成功')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '发送测试邮件' }))
  expect(await screen.findByText('测试邮件已发送')).toBeInTheDocument()
  expect(screen.getByText('连接成功')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '清除 SMTP 密码' }))

  await waitFor(() => expect(cleared).toBe(true))
  expect(connectionBodies).toEqual([''])
  expect(testEmailBodies).toEqual([''])
})

test('设置页显示失败投递安全摘要并可人工重试', async () => {
  const user = userEvent.setup()
  let retried = ''
  server.use(
    http.get('/api/v1/notifications/email-deliveries', () => HttpResponse.json([failedDelivery])),
    http.post('/api/v1/notifications/email-deliveries/:delivery_id/retry', ({ params }) => {
      retried = String(params.delivery_id)
      return HttpResponse.json({ ...failedDelivery, status: 'pending', attempt_count: 0 })
    }),
  )
  renderSettings()

  const table = await screen.findByRole('table', { name: '失败邮件投递' })
  expect(within(table).getByText('delivery-dead-001')).toBeInTheDocument()
  expect(within(table).getByText('连接超时，凭据已隐藏')).toBeInTheDocument()
  expect(screen.queryByText('smtp-password-secret')).not.toBeInTheDocument()
  await user.click(within(table).getByRole('button', { name: '重试 delivery-dead-001' }))
  await waitFor(() => expect(retried).toBe('delivery-dead-001'))
})

test('SMTP 配置和失败投递加载失败时显示独立降级状态', async () => {
  server.use(
    http.get('/api/v1/settings/notifications/email', () =>
      HttpResponse.json({ error: { code: 'smtp_unavailable', message: 'unavailable' } }, { status: 503 }),
    ),
    http.get('/api/v1/notifications/email-deliveries', () =>
      HttpResponse.json({ error: { code: 'delivery_unavailable', message: 'unavailable' } }, { status: 503 }),
    ),
  )
  renderSettings()

  expect(await screen.findByText('邮件配置加载失败')).toBeInTheDocument()
  expect(await screen.findByText('失败投递列表加载失败')).toBeInTheDocument()
  expect(screen.getByText(/本地通知仍可正常使用/)).toBeInTheDocument()
})
