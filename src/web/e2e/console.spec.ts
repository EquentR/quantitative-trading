import { expect, test } from '@playwright/test'

test('本地控制台框架可渲染并显示安全文案', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('qt_console_access_token', 'test-token')
  })

  await page.goto('/')

  await expect(page.getByRole('heading', { name: '今日仪表盘' })).toBeVisible()
  await expect(page.getByText('只做本地决策辅助，不自动真实下单')).toBeVisible()

  const width = page.viewportSize()?.width ?? 0
  if (width < 768) {
    await expect(page.getByRole('link', { name: '移动导航 准备' })).toBeVisible()
    await page.getByRole('link', { name: '移动导航 准备' }).click()
    await expect(page.getByRole('heading', { name: '准备' })).toBeVisible()
  }
})
