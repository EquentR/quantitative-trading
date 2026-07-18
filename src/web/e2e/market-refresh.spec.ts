import { expect, test, type Page } from '@playwright/test'
import { execFileSync, spawn, type ChildProcessByStdio } from 'node:child_process'
import { createServer } from 'node:net'
import { existsSync } from 'node:fs'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import type { Readable } from 'node:stream'
import { fileURLToPath } from 'node:url'

type Scenario = 'success' | 'partial'
type BackendProcess = ChildProcessByStdio<null, Readable, Readable>

interface Backend {
  baseUrl: string
  databasePath: string
  process: BackendProcess
  stderr: string[]
  tempDir: string
  token: string
}

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')
const serverScript = path.join(repoRoot, 'tests/support/market_refresh_e2e_server.py')
const venvPython = path.join(repoRoot, '.venv/bin/python')
const python = process.env.QT_E2E_PYTHON ?? (existsSync(venvPython) ? venvPython : 'python')

async function freePort(): Promise<number> {
  return await new Promise((resolve, reject) => {
    const server = createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (address === null || typeof address === 'string') {
        server.close()
        reject(new Error('failed to allocate E2E backend port'))
        return
      }
      server.close((error) => error ? reject(error) : resolve(address.port))
    })
  })
}

async function waitForBackend(baseUrl: string, process: BackendProcess, stderr: string[]) {
  const deadline = Date.now() + 15_000
  while (Date.now() < deadline) {
    if (process.exitCode !== null) {
      throw new Error(`E2E backend exited with ${process.exitCode}: ${stderr.join('')}`)
    }
    try {
      const response = await fetch(`${baseUrl}/openapi.json`)
      if (response.ok) return
    } catch {
      // The isolated backend is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 50))
  }
  throw new Error(`E2E backend did not start: ${stderr.join('')}`)
}

function scrubbedChildEnvironment(): NodeJS.ProcessEnv {
  return Object.fromEntries(
    Object.entries(process.env).filter(([key]) => !key.startsWith('QT_')),
  )
}

async function waitForExit(process: BackendProcess, timeoutMs: number): Promise<boolean> {
  if (process.exitCode !== null) return true
  return await new Promise((resolve) => {
    const onExit = () => {
      clearTimeout(timeout)
      resolve(true)
    }
    const timeout = setTimeout(() => {
      process.off('exit', onExit)
      resolve(false)
    }, timeoutMs)
    process.once('exit', onExit)
  })
}

async function terminateBackendProcess(process: BackendProcess) {
  if (process.exitCode !== null) return
  process.kill('SIGTERM')
  if (await waitForExit(process, 3_000)) return
  process.kill('SIGKILL')
  await waitForExit(process, 1_000)
}

async function startBackend(scenario: Scenario): Promise<Backend> {
  const tempDir = await mkdtemp(path.join(tmpdir(), 'qt-market-refresh-e2e-'))
  const databasePath = path.join(tempDir, 'market-refresh.db')
  const port = await freePort()
  const baseUrl = `http://127.0.0.1:${port}`
  const child = spawn(python, [
    serverScript,
    '--database', databasePath,
    '--port', String(port),
    '--scenario', scenario,
  ], {
    cwd: repoRoot,
    env: {
      ...scrubbedChildEnvironment(),
      PYTHONPATH: path.join(repoRoot, 'src'),
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  const stderr: string[] = []
  child.stderr.on('data', (chunk) => stderr.push(String(chunk)))
  try {
    await waitForBackend(baseUrl, child, stderr)
    const setup = await fetch(`${baseUrl}/api/v1/auth/setup-password`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ password: 'e2e-local-password' }),
    })
    if (!setup.ok) throw new Error(`E2E backend auth setup failed with ${setup.status}`)
    const login = await fetch(`${baseUrl}/api/v1/auth/login`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ password: 'e2e-local-password' }),
    })
    if (!login.ok) throw new Error(`E2E backend login failed with ${login.status}`)
    const body = await login.json() as { access_token: string }
    return { baseUrl, databasePath, process: child, stderr, tempDir, token: body.access_token }
  } catch (error) {
    await terminateBackendProcess(child)
    await rm(tempDir, { recursive: true, force: true })
    throw error
  }
}

async function stopBackend(backend: Backend) {
  await terminateBackendProcess(backend.process)
  await rm(backend.tempDir, { recursive: true, force: true })
}

async function authenticatePage(page: Page, backend: Backend) {
  await page.addInitScript(({ baseUrl, token }) => {
    localStorage.setItem('qt_console_api_base_url', baseUrl)
    localStorage.setItem('qt_console_access_token', token)
  }, { baseUrl: backend.baseUrl, token: backend.token })
}

async function expectMarketReady(page: Page) {
  await expect(page.getByRole('heading', { name: '确定性行情样本' })).toBeVisible()
  if ((page.viewportSize()?.width ?? 0) < 768) {
    await page.getByRole('button', { name: '选择决策标的' }).click()
    const drawer = page.getByRole('dialog', { name: '决策标的扫描器' })
    await expect(drawer.getByRole('button', { name: /600000/ })).toBeVisible()
    await drawer.getByRole('button', { name: /600000/ }).click()
  } else {
    await expect(page.getByRole('button', { name: /600000/ })).toBeVisible()
  }
}

async function apiPost(backend: Backend, pathname: string, body: unknown) {
  return await fetch(`${backend.baseUrl}/api/v1${pathname}`, {
    method: 'POST',
    headers: {
      authorization: `Bearer ${backend.token}`,
      'content-type': 'application/json',
    },
    body: JSON.stringify(body),
  })
}

async function apiGet(backend: Backend, pathname: string) {
  return await fetch(`${backend.baseUrl}/api/v1${pathname}`, {
    headers: { authorization: `Bearer ${backend.token}` },
  })
}

function decisionTableCounts(databasePath: string): Record<string, number> {
  const script = [
    'import json, sqlite3, sys',
    'db = sqlite3.connect(sys.argv[1])',
    "tables = ['account_snapshots', 'trading_plans', 'recommendations', 'notifications', 'email_deliveries', 'execution_feedback']",
    "print(json.dumps({name: db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0] for name in tables}))",
  ].join('; ')
  return JSON.parse(execFileSync(python, ['-c', script, databasePath], { encoding: 'utf8' }))
}

test('周末刷新跟随真实 409 且 display-only 不产生决策副作用', async ({ page }) => {
  const backend = await startBackend('success')
  try {
    await page.clock.setFixedTime(new Date('2026-07-18T02:01:30Z'))
    await authenticatePage(page, backend)
    const apiEvidence: string[] = []
    page.on('response', async (response) => {
      if (!/\/api\/v1\/(service\/workflows|market\/runs)/.test(response.url())) return
      apiEvidence.push(
        `${response.request().method()} ${response.status()} ${response.url()} ${await response.text()}`,
      )
    })
    await page.goto('/market')
    await expectMarketReady(page)
    const before = decisionTableCounts(backend.databasePath)

    const existingRun = apiPost(backend, '/service/workflows/backfill/run', {
      as_of_mode: 'latest_complete',
    })
    await expect.poll(async () => {
      const response = await apiGet(backend, '/market/runs?page=1&page_size=10')
      const body = await response.json() as { items: Array<{ workflow_type: string, status: string }> }
      return body.items.some((item) => item.workflow_type === 'backfill' && item.status === 'running')
    }).toBe(true)

    const conflict = page.waitForResponse((response) =>
      response.request().method() === 'POST'
      && response.url().endsWith('/service/workflows/backfill/run')
      && response.status() === 409)
    await page.getByRole('button', { name: '获取行情' }).click()
    await conflict
    try {
      await expect(page.getByRole('status')).toHaveText(
        '行情展示已刷新，数据部分可用，本次未生成交易建议',
        { timeout: 20_000 },
      )
    } catch (error) {
      throw new Error(`${String(error)}\nAPI evidence:\n${apiEvidence.join('\n')}`)
    }
    expect((await existingRun).ok).toBe(true)
    expect(decisionTableCounts(backend.databasePath)).toEqual(before)
  } finally {
    await stopBackend(backend)
  }
})

test('建议页通过真实 API 切换 current 和 history', async ({ page }) => {
  const backend = await startBackend('success')
  try {
    await authenticatePage(page, backend)
    await page.goto('/recommendations')
    await expect(page.getByText('当前样本新')).toBeVisible()
    await expect(page.getByText('历史样本旧')).toHaveCount(0)

    await page.getByRole('button', { name: '历史记录' }).click()
    await expect(page.getByText('当前样本新')).toBeVisible()
    await expect(page.getByText('历史样本旧')).toBeVisible()
  } finally {
    await stopBackend(backend)
  }
})

test('日 K 成功而分时降级时保留部分成功结果', async ({ page }) => {
  const backend = await startBackend('partial')
  try {
    await authenticatePage(page, backend)
    await page.goto('/market')
    await expectMarketReady(page)
    const before = decisionTableCounts(backend.databasePath)
    await page.getByRole('button', { name: '获取行情' }).click()

    await expect(page.getByRole('status')).toHaveText(
      '行情展示已刷新，数据部分可用，本次未生成交易建议',
      { timeout: 20_000 },
    )
    const runsResponse = await apiGet(backend, '/market/runs?page=1&page_size=10')
    const runs = await runsResponse.json() as {
      items: Array<{ workflow_type: string, status: string }>
    }
    expect(runs.items.some((item) =>
      item.workflow_type === 'backfill' && item.status === 'succeeded')).toBe(true)
    expect(runs.items.some((item) =>
      item.workflow_type === 'intraday' && item.status === 'degraded')).toBe(true)
    expect(decisionTableCounts(backend.databasePath)).toEqual(before)
    await page.getByRole('tab', { name: 'K 线' }).click()
    await expect(page.getByRole('img', { name: '前复权日 K 线、均线与成交量图' })).toBeVisible()
  } finally {
    await stopBackend(backend)
  }
})
