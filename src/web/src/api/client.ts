import type { ApiErrorPayload } from './types'

interface ApiClientOptions {
  baseUrl: string
  getToken: () => string | null
  onAuthError?: (error: ApiError) => void
}

interface RequestOptions {
  body?: unknown
  headers?: HeadersInit
  signal?: AbortSignal
}

export class ApiError extends Error {
  code: string
  details: unknown
  status: number

  constructor({
    code,
    message,
    details,
    status,
  }: {
    code: string
    message: string
    details: unknown
    status: number
  }) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.details = details
    this.status = status
  }
}

export class ApiClient {
  private readonly baseUrl: string
  private readonly getToken: () => string | null
  private readonly onAuthError?: (error: ApiError) => void

  constructor({ baseUrl, getToken, onAuthError }: ApiClientOptions) {
    this.baseUrl = baseUrl.replace(/\/$/, '')
    this.getToken = getToken
    this.onAuthError = onAuthError
  }

  get<T>(path: string, options: Omit<RequestOptions, 'body'> = {}): Promise<T> {
    return this.request<T>('GET', path, options)
  }

  post<T>(path: string, body?: unknown, options: Omit<RequestOptions, 'body'> = {}): Promise<T> {
    return this.request<T>('POST', path, { ...options, body })
  }

  put<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>('PUT', path, { body })
  }

  delete<T = null>(path: string): Promise<T> {
    return this.request<T>('DELETE', path)
  }

  async download(path: string): Promise<Blob> {
    const response = await fetch(this.url(path), {
      method: 'GET',
      headers: this.authHeaders(),
    })
    if (!response.ok) {
      await this.throwApiError(response, path)
    }
    return response.blob()
  }

  uploadCsv<T>(path: string, file: File): Promise<T> {
    const data = new FormData()
    data.append('file', file)
    return this.request<T>('POST', path, { body: data })
  }

  private async request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
    const headers = new Headers(options.headers)
    for (const [key, value] of this.authHeaders().entries()) {
      headers.set(key, value)
    }

    let body: BodyInit | undefined
    if (options.body instanceof FormData) {
      body = options.body
    } else if (options.body !== undefined) {
      headers.set('content-type', 'application/json')
      body = JSON.stringify(options.body)
    }

    const response = await fetch(this.url(path), {
      method,
      headers,
      body,
      signal: options.signal,
    })

    if (!response.ok) {
      await this.throwApiError(response)
    }

    if (response.status === 204) {
      return null as T
    }

    const text = await response.text()
    if (!text) {
      return null as T
    }
    return JSON.parse(text) as T
  }

  private url(path: string): string {
    const normalizedPath = path.startsWith('/') ? path : `/${path}`
    return `${this.baseUrl}/api/v1${normalizedPath}`
  }

  private authHeaders(): Headers {
    const headers = new Headers()
    const token = this.getToken()
    if (token) {
      headers.set('authorization', `Bearer ${token}`)
    }
    return headers
  }

  private async throwApiError(response: Response, path = ''): Promise<never> {
    let payload: ApiErrorPayload | null = null
    try {
      payload = (await response.json()) as ApiErrorPayload
    } catch {
      payload = null
    }

    const error = payload?.error
    const apiError = new ApiError({
      code: error?.code ?? `http_${response.status}`,
      message: error?.message ?? response.statusText,
      details: error?.details ?? {},
      status: response.status,
    })
    const isInvalidLogin = path === '/auth/login' && apiError.code === 'unauthorized'
    const shouldNotifyAuthError =
      !isInvalidLogin &&
      (apiError.code === 'unauthorized' || apiError.code === 'auth_setup_required' || response.status === 401)

    if (shouldNotifyAuthError) {
      this.onAuthError?.(apiError)
    }
    throw apiError
  }
}
