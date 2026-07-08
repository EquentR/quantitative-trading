import { ApiClient } from './client'
import { useSessionStore } from '@/stores/session'

export function useApiClient(): ApiClient {
  const session = useSessionStore()
  return new ApiClient({
    baseUrl: session.apiBaseUrl,
    getToken: () => session.token,
    onAuthError: (error) => {
      session.clearToken()
      window.dispatchEvent(
        new CustomEvent('qt-console-auth-error', {
          detail: { code: error.code },
        }),
      )
    },
  })
}
