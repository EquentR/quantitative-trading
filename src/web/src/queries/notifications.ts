import { useQuery } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import { pageItems } from '@/api/pagination'
import type { NotificationSummary, PaginatedResponse } from '@/api/types'

export const notificationsQueryKey = ['notifications'] as const

export function useNotificationsQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: notificationsQueryKey,
    queryFn: async () => pageItems(
      await client.get<PaginatedResponse<NotificationSummary> | NotificationSummary[]>('/notifications'),
    ),
    refetchInterval: 30_000,
    retry: false,
  })
}
