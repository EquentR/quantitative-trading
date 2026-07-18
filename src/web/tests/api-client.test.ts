import { delay, http, HttpResponse } from 'msw'
import { describe, expect, test } from 'vitest'
import { ApiClient, ApiError } from '@/api/client'
import { server } from '@/test/server'

describe('ApiClient', () => {
  test('注入 bearer token 并解析 JSON 响应', async () => {
    server.use(
      http.get('/api/v1/auth/me', ({ request }) => {
        expect(request.headers.get('authorization')).toBe('Bearer token-1')
        return HttpResponse.json({ user: 'local' })
      }),
    )

    const client = new ApiClient({ baseUrl: '', getToken: () => 'token-1' })

    await expect(client.get('/auth/me')).resolves.toEqual({ user: 'local' })
  })

  test('后端统一错误会转换成 ApiError', async () => {
    server.use(
      http.get('/api/v1/positions', () =>
        HttpResponse.json(
          { error: { code: 'unauthorized', message: 'login required', details: {} } },
          { status: 401 },
        ),
      ),
    )

    const client = new ApiClient({ baseUrl: '', getToken: () => null })

    await expect(client.get('/positions')).rejects.toMatchObject({
      code: 'unauthorized',
      message: 'login required',
      details: {},
      status: 401,
    } satisfies Partial<ApiError>)
  })

  test('unauthorized 错误会触发认证失效回调', async () => {
    let called = false
    server.use(
      http.get('/api/v1/auth/me', () =>
        HttpResponse.json(
          { error: { code: 'unauthorized', message: 'login required', details: {} } },
          { status: 401 },
        ),
      ),
    )

    const client = new ApiClient({
      baseUrl: '',
      getToken: () => 'expired-token',
      onAuthError: () => {
        called = true
      },
    })

    await expect(client.get('/auth/me')).rejects.toMatchObject({ code: 'unauthorized' })
    expect(called).toBe(true)
  })

  test('请求接收 AbortSignal 并中止 in-flight fetch', async () => {
    server.use(
      http.get('/api/v1/auth/me', async () => {
        await delay(1_000)
        return HttpResponse.json({ user: 'late' })
      }),
    )
    const client = new ApiClient({ baseUrl: '', getToken: () => 'token-1' })
    const controller = new AbortController()

    const request = client.get('/auth/me', { signal: controller.signal })
    controller.abort()

    await expect(request).rejects.toMatchObject({ name: 'AbortError' })
  })
})
