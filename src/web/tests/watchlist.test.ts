import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createPinia, setActivePinia } from 'pinia'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
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
  expect(panel.getByText('JSON/CSV 导入会全量替换当前观察池')).toBeInTheDocument()
})

test('从东方财富预览候选并只提交人工选中的代码', async () => {
  const user = userEvent.setup()
  const invalidateSpy = vi.spyOn(QueryClient.prototype, 'invalidateQueries')
  let previewCalls = 0
  let selectionPayload: unknown = null
  server.use(
    http.get('/api/v1/instruments/eastmoney-candidates', () => {
      previewCalls += 1
      return HttpResponse.json({
        preview_id: '11111111-1111-4111-8111-111111111111',
        source: 'eastmoney_watchlist',
        query: null,
        created_at: '2026-07-15T10:00:00+08:00',
        expires_at: '2026-07-15T10:10:00+08:00',
        warnings: ['已过滤 2 个非沪深 A 股或 ETF 品种'],
        items: [
          {
            symbol: '510300',
            name: '沪深300ETF',
            exchange: 'SH',
            instrument_type: 'etf',
            settlement_cycle: 't1',
            price_limit_ratio: 0.1,
            metadata_source: 'sse_etf_catalog',
            metadata_checked_at: '2026-07-15T09:00:00+08:00',
            rule_version: 'instrument-rules-v1',
            source: 'eastmoney_watchlist',
            source_rank: 1,
            already_monitored: false,
            selectable: true,
            warnings: [],
          },
        ],
      })
    }),
    http.post('/api/v1/watchlist/pinned/select', async ({ request }) => {
      selectionPayload = await request.json()
      return HttpResponse.json({ items: [], warnings: [] })
    }),
  )

  renderPreparation()
  const panel = watchPanel()
  await user.click(await panel.findByRole('button', { name: '从东方财富选择' }))

  await waitFor(() => expect(previewCalls).toBe(1))
  expect(await panel.findByText('510300')).toBeInTheDocument()
  const warningsToggle = panel.getByRole('button', { name: '目录校验提示 1 条' })
  expect(warningsToggle).toHaveAttribute('aria-expanded', 'false')
  expect(panel.queryByText('已过滤 2 个非沪深 A 股或 ETF 品种')).not.toBeInTheDocument()
  await user.click(warningsToggle)
  expect(panel.getByText('已过滤 2 个非沪深 A 股或 ETF 品种')).toBeInTheDocument()
  expect(warningsToggle).toHaveAttribute('aria-expanded', 'true')
  await user.click(panel.getByRole('checkbox', { name: '选择 510300' }))
  await user.click(panel.getByRole('button', { name: '加入监控' }))

  await waitFor(() => {
    expect(selectionPayload).toEqual({
      preview_id: '11111111-1111-4111-8111-111111111111',
      symbols: ['510300'],
    })
  })
  for (const queryKey of [
    ['watchlist', 'pinned'],
    ['universe'],
    ['plans'],
    ['recommendations'],
    ['service', 'status'],
    ['market'],
  ]) {
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey })
  }
  invalidateSpy.mockRestore()
})

test('证券搜索只在提交时请求并显示未知品种与未知结算限制', async () => {
  const user = userEvent.setup()
  const queries: string[] = []
  let eastmoneyCalls = 0
  server.use(
    http.get('/api/v1/instruments/eastmoney-candidates', () => {
      eastmoneyCalls += 1
      return HttpResponse.json({ items: [] })
    }),
    http.get('/api/v1/instruments/search', ({ request }) => {
      queries.push(new URL(request.url).searchParams.get('q') ?? '')
      return HttpResponse.json({
        preview_id: '22222222-2222-4222-8222-222222222222',
        source: 'instrument_search',
        query: '黄金 ETF',
        created_at: '2026-07-15T10:00:00+08:00',
        expires_at: '2026-07-15T10:10:00+08:00',
        warnings: [],
        items: [
          {
            symbol: '518880', name: '黄金ETF', exchange: 'SH', instrument_type: 'etf',
            settlement_cycle: 'unknown', price_limit_ratio: null,
            metadata_source: 'sse_etf_catalog', metadata_checked_at: '2026-07-15T09:00:00+08:00',
            rule_version: 'instrument-rules-v1', source: 'instrument_search', source_rank: null,
            already_monitored: false, selectable: true, warnings: ['交易制度待确认'],
          },
          {
            symbol: '160000', name: '未知基金', exchange: null, instrument_type: 'unknown',
            settlement_cycle: 'unknown', price_limit_ratio: null,
            metadata_source: 'instrument_directory', metadata_checked_at: '2026-07-15T09:00:00+08:00',
            rule_version: 'instrument-rules-v1', source: 'instrument_search', source_rank: null,
            already_monitored: false, selectable: false, warnings: ['证券类型无法验证'],
          },
        ],
      })
    }),
  )

  renderPreparation()
  const panel = watchPanel()
  await user.click(await panel.findByRole('button', { name: '按名称或代码搜索' }))
  const input = await panel.findByRole('searchbox', { name: '股票名称或代码' })
  await user.type(input, '  黄金 ETF  ')
  expect(queries).toEqual([])
  await user.click(panel.getByRole('button', { name: '搜索证券' }))

  await waitFor(() => expect(queries).toEqual(['黄金 ETF']))
  expect(eastmoneyCalls).toBe(0)
  expect(panel.getByText('仅观察，交易制度待确认')).toBeInTheDocument()
  expect(panel.getByRole('checkbox', { name: '选择 518880' })).toBeEnabled()
  expect(panel.getByRole('checkbox', { name: '选择 160000' })).toBeDisabled()
})

test('东方财富候选缺少配置时只请求一次并显示稳定错误', async () => {
  const user = userEvent.setup()
  let calls = 0
  server.use(
    http.get('/api/v1/instruments/eastmoney-candidates', () => {
      calls += 1
      return HttpResponse.json(
        { error: { code: 'datasource_not_configured', message: 'missing', details: {} } },
        { status: 409 },
      )
    }),
  )

  renderPreparation()
  const panel = watchPanel()
  await user.click(await panel.findByRole('button', { name: '从东方财富选择' }))

  await waitFor(() => expect(panel.getByText('东方财富数据源尚未配置，请先保存 API Key')).toBeInTheDocument())
  expect(calls).toBe(1)
})

test.each([
  ['无效 Key', 'datasource_invalid', 424, '东方财富 API Key 无效，请重新配置'],
  ['额度耗尽', 'datasource_quota_exceeded', 429, '东方财富调用额度已耗尽，请稍后再试'],
  ['网络不可用', 'datasource_unavailable', 503, '东方财富网络连接不可用，请稍后重试'],
  ['供应商契约变化', 'datasource_contract_error', 502, '东方财富响应格式已变化，暂时无法读取候选'],
  ['证券目录不可用', 'instrument_directory_unavailable', 503, '证券目录暂不可用，请稍后重试'],
])('东方财富候选处理%s错误', async (_caseName, code, status, expectedMessage) => {
  const user = userEvent.setup()
  let calls = 0
  server.use(
    http.get('/api/v1/instruments/eastmoney-candidates', () => {
      calls += 1
      return HttpResponse.json(
        { error: { code, message: code, details: {} } },
        { status },
      )
    }),
  )

  renderPreparation()
  const panel = watchPanel()
  await user.click(await panel.findByRole('button', { name: '从东方财富选择' }))

  await waitFor(() => expect(panel.getByText(expectedMessage)).toBeInTheDocument())
  expect(calls).toBe(1)
})

test('东方财富候选成功空结果显示独立空状态', async () => {
  const user = userEvent.setup()
  server.use(
    http.get('/api/v1/instruments/eastmoney-candidates', () =>
      HttpResponse.json({
        preview_id: '33333333-3333-4333-8333-333333333333',
        source: 'eastmoney_watchlist',
        query: null,
        created_at: '2026-07-15T10:00:00+08:00',
        expires_at: '2026-07-15T10:10:00+08:00',
        items: [],
        warnings: [],
      }),
    ),
  )

  renderPreparation()
  const panel = watchPanel()
  await user.click(await panel.findByRole('button', { name: '从东方财富选择' }))

  expect(await panel.findByText('未找到可展示的候选证券。')).toBeInTheDocument()
})

test('确认时预览过期会清空候选并要求重新获取', async () => {
  const user = userEvent.setup()
  server.use(
    http.post('/api/v1/watchlist/pinned/select', () =>
      HttpResponse.json(
        { error: { code: 'instrument_preview_expired', message: 'expired', details: {} } },
        { status: 410 },
      ),
    ),
  )

  renderPreparation()
  const panel = watchPanel()
  await user.click(await panel.findByRole('button', { name: '从东方财富选择' }))
  await user.click(await panel.findByRole('checkbox', { name: '选择 510300' }))
  await user.click(panel.getByRole('button', { name: '加入监控' }))

  await waitFor(() => expect(panel.getByText('候选预览已过期，请重新获取')).toBeInTheDocument())
  expect(panel.queryByText('510300')).not.toBeInTheDocument()
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
  let responseMode = ''
  server.use(
    http.post('/api/v1/watchlist/pinned/import', async ({ request }) => {
      captured = await request.json()
      responseMode = new URL(request.url).searchParams.get('response') ?? ''
      return HttpResponse.json({
        items: [],
        warnings: ['600888 instrument metadata is unavailable or unverified; plan remains disabled'],
      })
    }),
  )

  renderPreparation()

  await user.click(await screen.findByRole('button', { name: '导入自选观察项' }))

  const textarea = screen.getByLabelText('自选 JSON 内容')
  const json = '[{"symbol":"600888","name":"导入测试","rank":2,"plan_enabled":true,"note":""}]'
  await fireEvent.update(textarea, json)
  await user.click(screen.getByRole('button', { name: '保存导入自选' }))

  await waitFor(() => {
    expect(responseMode).toBe('envelope')
    expect(captured).toEqual({
      items: [{ symbol: '600888', name: '导入测试', rank: 2, plan_enabled: true, note: '' }],
    })
  })
  expect(screen.getByText('600888 instrument metadata is unavailable or unverified; plan remains disabled')).toBeInTheDocument()
})

test('编辑现有记录后提交 PUT 到正确路径与完整请求体', async () => {
  const user = userEvent.setup()
  let capturedUrl = ''
  let captured: unknown = null
  server.use(
    http.put('/api/v1/watchlist/pinned/:symbol', async ({ request, params }) => {
      capturedUrl = String(params.symbol)
      captured = await request.json()
      return HttpResponse.json({ ...(captured as Record<string, unknown>), source: 'manual', updated_at: '2026-07-07T10:30:00+08:00' })
    }),
  )

  renderPreparation()
  const panel = watchPanel()

  const editBtn = await panel.findByRole('button', { name: '编辑' })
  await user.click(editBtn)

  await user.clear(panel.getByLabelText('股票名称'))
  await user.type(panel.getByLabelText('股票名称'), '编辑后名称')
  await user.clear(panel.getByLabelText('备注'))
  await user.type(panel.getByLabelText('备注'), '编辑后备注')

  await user.click(panel.getByRole('button', { name: '保存自选观察项' }))

  await waitFor(() => {
    expect(capturedUrl).toBe('600519')
    expect(captured).toEqual({
      symbol: '600519',
      name: '编辑后名称',
      rank: 1,
      plan_enabled: true,
      note: '编辑后备注',
    })
  })
})

test('CSV 导入选择文件后调用 import-csv 接口', async () => {
  renderPreparation()
  const panel = watchPanel()

  let csvCalled = false
  let responseMode = ''
  server.use(
    http.post('/api/v1/watchlist/pinned/import-csv', ({ request }) => {
      csvCalled = true
      responseMode = new URL(request.url).searchParams.get('response') ?? ''
      return HttpResponse.json({
        items: [],
        warnings: ['600888 instrument metadata is unavailable or unverified; plan remains disabled'],
      })
    }),
  )

  const fileInput = panel.getByLabelText('CSV 文件输入') as HTMLInputElement
  const file = new File(['symbol,name,rank,plan_enabled,note\n600888,CSV测试,3,true,'], 'watch.csv', { type: 'text/csv' })
  await fireEvent.change(fileInput, { target: { files: [file] } })

  await waitFor(() => {
    expect(csvCalled).toBe(true)
    expect(responseMode).toBe('envelope')
  })
  expect(screen.getByText('600888 instrument metadata is unavailable or unverified; plan remains disabled')).toBeInTheDocument()
})

test('JSON 导入失败显示错误并保留输入供修正', async () => {
  const user = userEvent.setup()
  server.use(
    http.post('/api/v1/watchlist/pinned/import', () =>
      HttpResponse.json(
        { error: { code: 'validation_error', message: 'request validation failed', details: {} } },
        { status: 422 },
      ),
    ),
  )

  renderPreparation()
  await user.click(await screen.findByRole('button', { name: '导入自选观察项' }))
  const textarea = screen.getByLabelText('自选 JSON 内容')
  const json = '[{"symbol":"600888","name":"待修正","rank":2,"plan_enabled":true,"note":""}]'
  await fireEvent.update(textarea, json)
  await user.click(screen.getByRole('button', { name: '保存导入自选' }))

  expect(await screen.findByText('导入失败：导入内容不符合要求，请检查代码、排序和字段格式')).toBeInTheDocument()
  expect(textarea).toHaveValue(json)
})

test('CSV 导入失败显示错误并复位文件输入', async () => {
  server.use(
    http.post('/api/v1/watchlist/pinned/import-csv', () => HttpResponse.error()),
  )

  renderPreparation()
  const fileInput = watchPanel().getByLabelText('CSV 文件输入') as HTMLInputElement
  const file = new File(['symbol,name,rank,plan_enabled,note\n600888,CSV测试,3,true,'], 'watch.csv', { type: 'text/csv' })
  await fireEvent.change(fileInput, { target: { files: [file] } })

  expect(await screen.findByText('导入失败，请稍后重试')).toBeInTheDocument()
  expect(fileInput).toHaveValue('')
})

test('CSV 导出点击后触发下载路径', async () => {
  const user = userEvent.setup()
  renderPreparation()
  const panel = watchPanel()

  let exported = false
  server.use(
    http.get('/api/v1/watchlist/pinned/export-csv', () => {
      exported = true
      return new HttpResponse('symbol,name\n600519,示例白酒\n', { headers: { 'content-type': 'text/csv' } })
    }),
  )

  const createUrl = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:test')
  const revokeUrl = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

  await user.click(panel.getByRole('button', { name: '导出 CSV 自选' }))

  await waitFor(() => expect(exported).toBe(true))
  await waitFor(() => expect(createUrl).toHaveBeenCalledTimes(1))
  expect(clickSpy).toHaveBeenCalledTimes(1)
  await waitFor(() => expect(revokeUrl).toHaveBeenCalledTimes(1))

  createUrl.mockRestore()
  revokeUrl.mockRestore()
  clickSpy.mockRestore()
})

test('JSON 导入接受 { items } 信封并原样发送 { items }', async () => {
  const user = userEvent.setup()
  let captured: unknown = null
  server.use(
    http.post('/api/v1/watchlist/pinned/import', async ({ request }) => {
      captured = await request.json()
      return HttpResponse.json({ items: [], warnings: [] })
    }),
  )

  renderPreparation()

  await user.click(await screen.findByRole('button', { name: '导入自选观察项' }))

  const textarea = screen.getByLabelText('自选 JSON 内容')
  const json = '{"items":[{"symbol":"600777","name":"信封测试","rank":4,"plan_enabled":false,"note":"envelope"}]}'
  await fireEvent.update(textarea, json)
  await user.click(screen.getByRole('button', { name: '保存导入自选' }))

  await waitFor(() => {
    expect(captured).toEqual({
      items: [{ symbol: '600777', name: '信封测试', rank: 4, plan_enabled: false, note: 'envelope' }],
    })
  })
})

test('JSON 导入无效 JSON 时显示校验错误且不调用 import 接口', async () => {
  const user = userEvent.setup()
  let importCalled = false
  server.use(
    http.post('/api/v1/watchlist/pinned/import', () => {
      importCalled = true
      return HttpResponse.json([])
    }),
  )

  renderPreparation()

  await user.click(await screen.findByRole('button', { name: '导入自选观察项' }))

  const textarea = screen.getByLabelText('自选 JSON 内容')
  await fireEvent.update(textarea, 'not-valid-json{{{')
  await user.click(screen.getByRole('button', { name: '保存导入自选' }))

  // Visible validation error appears.
  await waitFor(() => expect(screen.getByText(/JSON 格式错误/)).toBeInTheDocument())

  // Import API must not be called.
  expect(importCalled).toBe(false)

  // Form stays open for correction.
  expect(screen.getByLabelText('自选 JSON 内容')).toBeInTheDocument()
})

test('JSON 导入不支持的信封格式时显示校验错误且不调用 import 接口', async () => {
  const user = userEvent.setup()
  let importCalled = false
  server.use(
    http.post('/api/v1/watchlist/pinned/import', () => {
      importCalled = true
      return HttpResponse.json([])
    }),
  )

  renderPreparation()

  await user.click(await screen.findByRole('button', { name: '导入自选观察项' }))

  const textarea = screen.getByLabelText('自选 JSON 内容')
  await fireEvent.update(textarea, '{"watchlist": [{"symbol":"600000","name":"x","rank":1,"plan_enabled":true,"note":""}]}')
  await user.click(screen.getByRole('button', { name: '保存导入自选' }))

  // Visible validation error appears.
  await waitFor(() => expect(screen.getByText(/不支持的信封格式/)).toBeInTheDocument())

  // Import API must not be called.
  expect(importCalled).toBe(false)

  // Form stays open for correction.
  expect(screen.getByLabelText('自选 JSON 内容')).toBeInTheDocument()
})
