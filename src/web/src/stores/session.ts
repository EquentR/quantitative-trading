import { defineStore } from 'pinia'

export const API_BASE_URL_KEY = 'qt_console_api_base_url'
export const ACCESS_TOKEN_KEY = 'qt_console_access_token'
const TABLE_DENSITY_KEY = 'qt_console_table_density'

type TableDensity = 'comfortable' | 'compact'

function readStorage(key: string): string | null {
  return globalThis.localStorage?.getItem(key) ?? null
}

export const useSessionStore = defineStore('session', {
  state: () => ({
    apiBaseUrl: readStorage(API_BASE_URL_KEY) ?? '',
    token: readStorage(ACCESS_TOKEN_KEY),
    tableDensity: (readStorage(TABLE_DENSITY_KEY) as TableDensity | null) ?? 'comfortable',
  }),
  actions: {
    setApiBaseUrl(value: string) {
      const normalized = value.trim().replace(/\/$/, '')
      this.apiBaseUrl = normalized
      localStorage.setItem(API_BASE_URL_KEY, normalized)
    },
    setToken(value: string) {
      this.token = value
      localStorage.setItem(ACCESS_TOKEN_KEY, value)
    },
    clearToken() {
      this.token = null
      localStorage.removeItem(ACCESS_TOKEN_KEY)
    },
    setTableDensity(value: TableDensity) {
      this.tableDensity = value
      localStorage.setItem(TABLE_DENSITY_KEY, value)
    },
  },
})
