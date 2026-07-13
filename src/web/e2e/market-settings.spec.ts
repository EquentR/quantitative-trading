import { expect, test, type Page, type Route } from '@playwright/test'
import {
  mockDailyBars,
  mockEmailDeliveries,
  mockEmailSettings,
  mockIntradayStrength,
  mockMarketOverview,
  mockMarketSymbols,
  mockMarketTrace,
  mockMinuteBars,
  mockMoneyFlow,
  mockServiceStatus,
} from '../src/mocks/handlers'

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function setupMarketConsole(page: Page) {
  await page.addInitScript(() => localStorage.setItem('qt_console_access_token', 'test-token'))

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace('/api/v1', '')

    if (path === '/service/status') return fulfillJson(route, mockServiceStatus)
    if (path === '/market/symbols') return fulfillJson(route, { items: mockMarketSymbols, total: mockMarketSymbols.length })
    if (/^\/market\/symbols\/\d{6}\/overview$/.test(path)) {
      const symbol = path.split('/').at(-2)
      return fulfillJson(route, {
        ...mockMarketOverview,
        symbol,
        name: symbol === '600519' ? '示例白酒' : mockMarketOverview.name,
      })
    }
    if (/^\/market\/symbols\/\d{6}\/daily-bars$/.test(path)) return fulfillJson(route, mockDailyBars)
    if (/^\/market\/symbols\/\d{6}\/money-flow$/.test(path)) return fulfillJson(route, mockMoneyFlow)
    if (/^\/market\/symbols\/\d{6}\/minute-bars$/.test(path)) return fulfillJson(route, mockMinuteBars)
    if (/^\/market\/symbols\/\d{6}\/intraday-strength\/latest$/.test(path)) return fulfillJson(route, mockIntradayStrength)
    if (/^\/market\/snapshots\/[^/]+\/trace$/.test(path)) return fulfillJson(route, mockMarketTrace)

    return fulfillJson(route, { error: { code: 'not_found', message: path } }, 404)
  })
}

test('行情工作台在桌面和移动视口渲染图表并切换标的', async ({ page }) => {
  await setupMarketConsole(page)
  await page.goto('/market')

  await expect(page.getByRole('heading', { name: '行情' })).toBeVisible()
  const mobile = (page.viewportSize()?.width ?? 0) < 768

  if (mobile) {
    await page.getByRole('button', { name: '选择决策标的' }).click()
    const drawer = page.getByRole('dialog', { name: '决策标的扫描器' })
    await expect(drawer).toBeVisible()
    await drawer.getByRole('button', { name: /600519 示例白酒/ }).click()
    await expect(page.getByRole('heading', { name: '示例白酒' })).toBeVisible()
  } else {
    await expect(page.getByRole('button', { name: /600000 示例银行/ })).toBeVisible()
  }

  await page.getByRole('tab', { name: 'K 线' }).click()
  const dailyChart = page.getByRole('img', { name: '前复权日 K 线、均线与成交量图' })
  await expect(dailyChart).toBeVisible()
  await expect(dailyChart.locator('svg')).toBeVisible()
  expect((await dailyChart.boundingBox())?.height).toBeGreaterThanOrEqual(288)

  await page.getByRole('tab', { name: '资金流' }).click()
  await expect(page.getByRole('img', { name: '资金流净额与占比图' }).locator('svg')).toBeVisible()

  await page.getByRole('tab', { name: '分时强弱' }).click()
  await expect(page.getByRole('img', { name: '分时价格、VWAP 与成交量图' }).locator('svg')).toBeVisible()
  await expect(page.getByText('建议发生点 10:18 watch')).toBeVisible()

  await page.getByRole('tab', { name: '数据引用' }).click()
  await expect(page.getByText('run-20260713-001')).toBeVisible()
  await expect(page.getByText('snapshot-101')).toBeVisible()
})

test('邮件设置保持密码脱敏并支持测试和失败投递重试', async ({ page }) => {
  let retriedDelivery = ''
  const connectionBodies: Array<string | null> = []
  const testEmailBodies: Array<string | null> = []

  await page.addInitScript(() => localStorage.setItem('qt_console_access_token', 'test-token'))
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace('/api/v1', '')

    if (path === '/service/status') return fulfillJson(route, mockServiceStatus)
    if (request.method() === 'GET' && path === '/settings/notifications/email') return fulfillJson(route, mockEmailSettings)
    if (request.method() === 'GET' && path === '/notifications/email-deliveries') {
      return fulfillJson(route, mockEmailDeliveries)
    }
    if (request.method() === 'POST' && path === '/notifications/email/settings/test-connection') {
      connectionBodies.push(request.postData())
      return fulfillJson(route, { status: 'connected' })
    }
    if (request.method() === 'POST' && path === '/settings/notifications/email/test') {
      testEmailBodies.push(request.postData())
      return fulfillJson(route, { status: 'sent' })
    }
    if (request.method() === 'POST' && path.endsWith('/retry')) {
      retriedDelivery = path.split('/').at(-2) ?? ''
      return fulfillJson(route, { status: 'pending' })
    }

    return fulfillJson(route, { error: { code: 'not_found', message: path } }, 404)
  })

  await page.goto('/settings')
  await expect(page.getByRole('heading', { name: '邮件通知' })).toBeVisible()
  await expect(page.getByText('密码已配置')).toBeVisible()
  await expect(page.getByLabel('SMTP 密码')).toHaveValue('')
  await expect(page.getByText(/数据库导出和备份会包含 SMTP 明文密码/)).toBeVisible()

  const connectionButton = page.getByRole('button', { name: '测试 SMTP 连接' })
  await connectionButton.click()
  await expect(page.getByText('连接成功')).toBeVisible()
  await page.getByRole('button', { name: '发送测试邮件' }).click()
  await expect(page.getByText('测试邮件已发送')).toBeVisible()
  await expect(page.getByText('连接成功')).toBeVisible()
  expect(connectionBodies).toEqual([null])
  expect(testEmailBodies).toEqual([null])

  await page.getByRole('button', { name: '重试 delivery-dead-001' }).click()
  await expect.poll(() => retriedDelivery).toBe('delivery-dead-001')
  await expect(page.getByText('连接超时，凭据已隐藏')).toBeVisible()
})
