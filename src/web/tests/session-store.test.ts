import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, expect, test } from 'vitest'
import { useSessionStore } from '@/stores/session'

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
})

test('保存 token 和 API 地址到 localStorage', () => {
  const session = useSessionStore()

  session.setApiBaseUrl('http://127.0.0.1:8000')
  session.setToken('abc')

  expect(session.apiBaseUrl).toBe('http://127.0.0.1:8000')
  expect(session.token).toBe('abc')
  expect(localStorage.getItem('qt_console_api_base_url')).toBe('http://127.0.0.1:8000')
  expect(localStorage.getItem('qt_console_access_token')).toBe('abc')
})

test('清除 token 不清除 API 地址', () => {
  const session = useSessionStore()
  session.setApiBaseUrl('http://localhost:8000')
  session.setToken('abc')

  session.clearToken()

  expect(session.apiBaseUrl).toBe('http://localhost:8000')
  expect(session.token).toBeNull()
})
