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
  mockAuditLog,
  mockNotifications,
  mockRecommendations,
  mockServiceStatus,
} from '../src/mocks/handlers'

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

interface MarketConsoleOptions {
  staleLongContent?: boolean
}

const longName = '一个非常长但必须完整换行并保持在当前标的详情区域内的示例银行名称'
const longWarning = '日 K 数据已经超过允许时效，这是一条用于验证窄屏换行、容器约束和图表之间不发生不合理重叠的较长质量告警。'

async function setupMarketConsole(page: Page, options: MarketConsoleOptions = {}) {
  const recommendationId = mockMarketOverview.recommendation!.recommendation_id
  const recommendation = { ...mockRecommendations[0], recommendation_id: recommendationId }
  const notification = { ...mockNotifications[0], recommendation_id: recommendationId }
  await page.addInitScript(() => localStorage.setItem('qt_console_access_token', 'test-token'))

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace('/api/v1', '')

    if (path === '/service/status') return fulfillJson(route, mockServiceStatus)
    if (path === '/market/symbols') {
      const items = options.staleLongContent
        ? [{ ...mockMarketSymbols[0], name: longName }, ...mockMarketSymbols.slice(1)]
        : mockMarketSymbols
      return fulfillJson(route, { items, total: items.length })
    }
    if (/^\/market\/symbols\/\d{6}\/overview$/.test(path)) {
      const symbol = path.split('/').at(-2)
      return fulfillJson(route, {
        ...mockMarketOverview,
        symbol,
        name: symbol === '600519' ? '示例白酒' : options.staleLongContent ? longName : mockMarketOverview.name,
      })
    }
    if (/^\/market\/symbols\/\d{6}\/daily-bars$/.test(path)) {
      return fulfillJson(route, options.staleLongContent
        ? { ...mockDailyBars, status: 'stale', warnings: [longWarning] }
        : mockDailyBars)
    }
    if (/^\/market\/symbols\/\d{6}\/money-flow$/.test(path)) return fulfillJson(route, mockMoneyFlow)
    if (/^\/market\/symbols\/\d{6}\/minute-bars$/.test(path)) return fulfillJson(route, mockMinuteBars)
    if (/^\/market\/symbols\/\d{6}\/intraday-strength\/latest$/.test(path)) return fulfillJson(route, mockIntradayStrength)
    if (/^\/market\/snapshots\/[^/]+\/trace$/.test(path)) return fulfillJson(route, mockMarketTrace)
    if (path === '/recommendations') return fulfillJson(route, {
      items: [{
        recommendation,
        notification: { notification_id: notification.notification_id, status: notification.status },
      }],
      total: 1,
      page: 1,
      page_size: 20,
    })
    if (path === `/recommendations/${recommendationId}`) return fulfillJson(route, recommendation)
    if (path === '/notifications') return fulfillJson(route, {
      items: [notification], total: 1, page: 1, page_size: 50,
    })
    if (path === '/audit') return fulfillJson(route, [{ ...mockAuditLog, recommendation_id: recommendationId }])

    return fulfillJson(route, { error: { code: 'not_found', message: path } }, 404)
  })
}

async function expectNonEmptyChart(chart: ReturnType<Page['getByRole']>) {
  await expect(chart.locator('svg, canvas')).toBeVisible()
  const evidence = await chart.evaluate((element) => {
    const canvas = element.querySelector('canvas')
    if (canvas) {
      const context = canvas.getContext('2d')
      const pixels = context?.getImageData(0, 0, canvas.width, canvas.height).data ?? []
      return { visibleElements: 0, nonTransparentPixels: Array.from(pixels).filter((_, index) => index % 4 === 3 && pixels[index] > 0).length }
    }
    const svg = element.querySelector('svg')
    const visibleElements = Array.from(svg?.querySelectorAll<SVGGraphicsElement>('path, rect, line, polyline, polygon, circle, text') ?? [])
      .filter((item) => {
        try {
          const box = item.getBBox()
          const style = getComputedStyle(item)
          return box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.opacity !== '0'
        } catch {
          return false
        }
      }).length
    return { visibleElements, nonTransparentPixels: 0 }
  })
  expect(evidence.visibleElements + evidence.nonTransparentPixels).toBeGreaterThan(10)
}

async function expectLegendAndTooltip(page: Page, chart: ReturnType<Page['getByRole']>) {
  const svg = chart.locator('svg')
  const legend = svg.locator('text').filter({ hasText: /^MA5$/ })
  await expect(legend).toBeVisible()
  const before = await svg.innerHTML()
  await legend.click()
  await page.waitForTimeout(150)
  expect(await svg.innerHTML()).not.toBe(before)
  await legend.click()

  const box = await chart.boundingBox()
  expect(box).not.toBeNull()
  let tooltipText = ''
  for (const fraction of [0.2, 0.35, 0.5, 0.65, 0.8]) {
    await page.mouse.move(box!.x + box!.width * fraction, box!.y + box!.height * 0.4)
    await page.waitForTimeout(100)
    tooltipText = await chart.evaluate((element) => Array.from(element.querySelectorAll('div'))
      .find((item) => {
        const style = getComputedStyle(item)
        return style.position === 'absolute' && style.display !== 'none' && style.visibility !== 'hidden' && Boolean(item.textContent?.trim())
      })?.textContent ?? '')
    if (/日 K|MA5/.test(tooltipText)) break
  }
  expect(tooltipText).toMatch(/日 K|MA5/)
}

test('行情工作台在桌面和移动视口渲染图表并切换标的', async ({ page }) => {
  await setupMarketConsole(page)
  await page.goto('/market')

  await expect(page.getByRole('heading', { name: '行情' })).toBeVisible()
  const tabs = page.getByRole('tab')
  await expect(tabs).toHaveCount(5)
  await expect(page.getByRole('tab', { name: '概览' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('heading', { name: '市场结构' })).toBeVisible()
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
  await expectNonEmptyChart(dailyChart)
  await expectLegendAndTooltip(page, dailyChart)
  expect((await dailyChart.boundingBox())?.height).toBeGreaterThanOrEqual(288)

  await page.getByRole('tab', { name: '资金流' }).click()
  await expectNonEmptyChart(page.getByRole('img', { name: '资金流净额与占比图' }))

  await page.getByRole('tab', { name: '分时强弱' }).click()
  await expectNonEmptyChart(page.getByRole('img', { name: '分时价格、VWAP 与成交量图' }))
  await expect(page.getByText('建议发生点 10:18 watch')).toBeVisible()

  await page.getByRole('tab', { name: '数据引用' }).click()
  await expect(page.getByText('run-20260713-001')).toBeVisible()
  await expect(page.getByText('snapshot-101')).toBeVisible()
})

test('陈旧图表和长文本在桌面与移动视口内有明确且不重叠的质量标记', async ({ page }) => {
  await setupMarketConsole(page, { staleLongContent: true })
  await page.goto('/market?symbol=600000')

  const heading = page.getByRole('heading', { name: longName })
  await expect(heading).toBeVisible()
  await page.getByRole('tab', { name: 'K 线' }).click()
  const warning = page.getByText(longWarning)
  const chart = page.getByRole('img', { name: '前复权日 K 线、均线与成交量图' })
  const marker = chart.getByText(/陈旧数据/)
  await expect(warning).toBeVisible()
  await expect(marker).toBeVisible()
  await expectNonEmptyChart(chart)

  const [headingBox, warningBox, chartBox, markerBox] = await Promise.all([
    heading.boundingBox(), warning.boundingBox(), chart.boundingBox(), marker.boundingBox(),
  ])
  expect(headingBox).not.toBeNull()
  expect(warningBox).not.toBeNull()
  expect(chartBox).not.toBeNull()
  expect(markerBox).not.toBeNull()
  expect(warningBox!.y + warningBox!.height).toBeLessThanOrEqual(chartBox!.y + 1)
  expect(markerBox!.x).toBeGreaterThanOrEqual(chartBox!.x)
  expect(markerBox!.x + markerBox!.width).toBeLessThanOrEqual(chartBox!.x + chartBox!.width + 1)
  const viewportWidth = page.viewportSize()!.width
  expect(headingBox!.x + headingBox!.width).toBeLessThanOrEqual(viewportWidth)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
})

test('行情认证过期时清除会话并重定向登录', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('qt_console_access_token', 'expired-token'))
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '')
    if (path === '/service/status') return fulfillJson(route, mockServiceStatus)
    if (path === '/market/symbols') {
      return fulfillJson(route, { error: { code: 'unauthorized', message: 'expired', details: {} } }, 401)
    }
    return fulfillJson(route, { error: { code: 'not_found', message: path } }, 404)
  })

  await page.goto('/market')
  await expect(page).toHaveURL(/\/login\?redirect=\/market$/)
  await expect(page.getByRole('heading', { name: '登录' })).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('qt_console_access_token'))).toBeNull()
})

test('行情建议 ID 可定位建议详情并返回同一标的行情', async ({ page }) => {
  await setupMarketConsole(page)
  await page.goto('/market?symbol=600000')

  const recommendationId = mockMarketOverview.recommendation!.recommendation_id
  await page.getByRole('link', { name: recommendationId }).click()
  await expect(page).toHaveURL(new RegExp(`/recommendations\\?recommendation_id=${recommendationId}$`))
  const drawer = page.getByRole('dialog', { name: '建议详情' })
  await expect(drawer).toBeVisible()

  await drawer.getByRole('link', { name: '返回 600000 行情' }).click()
  await expect(page).toHaveURL(/\/market\?symbol=600000$/)
  await expect(page.getByRole('heading', { name: '示例银行' })).toBeVisible()
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
