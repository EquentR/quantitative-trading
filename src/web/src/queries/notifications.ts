import { useQuery } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import type { NotificationSummary } from '@/api/types'

export const notificationsQueryKey = ['notifications'] as const

export function useNotificationsQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: notificationsQueryKey,
    queryFn: () => client.get<NotificationSummary[]>('/notifications'),
    retry: false,
  })
}
