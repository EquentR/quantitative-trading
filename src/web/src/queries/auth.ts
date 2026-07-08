import { computed } from 'vue'
import { useMutation, useQuery } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import type { AuthStatus, LoginResponse } from '@/api/types'
import { useSessionStore } from '@/stores/session'

interface PasswordPayload {
  password: string
}

export function useSetupPasswordMutation() {
  const client = useApiClient()
  return useMutation({
    mutationFn: (payload: PasswordPayload) =>
      client.post<{ auth_status: AuthStatus }>('/auth/setup-password', payload),
  })
}

export function useLoginMutation() {
  const client = useApiClient()
  return useMutation({
    mutationFn: (payload: PasswordPayload) => client.post<LoginResponse>('/auth/login', payload),
  })
}

export function useLogoutMutation() {
  const client = useApiClient()
  return useMutation({
    mutationFn: () => client.post<{ status: string }>('/auth/logout'),
  })
}

export function useMeQuery() {
  const client = useApiClient()
  const session = useSessionStore()
  return useQuery({
    queryKey: ['auth', 'me'],
    queryFn: () => client.get<{ user: string }>('/auth/me'),
    enabled: computed(() => Boolean(session.token)),
    retry: false,
  })
}
