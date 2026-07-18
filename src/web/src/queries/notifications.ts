import { useQuery } from '@tanstack/vue-query'
import { computed, toValue, type MaybeRefOrGetter } from 'vue'
import { useApiClient } from '@/api/client-provider'
import { pageItems } from '@/api/pagination'
import type { NotificationSummary, PaginatedResponse, RecommendationView } from '@/api/types'

export const notificationsQueryKey = ['notifications'] as const

export function useNotificationsQuery(
  view: MaybeRefOrGetter<RecommendationView> = 'current',
) {
  const client = useApiClient()
  return useQuery({
    queryKey: computed(() => [...notificationsQueryKey, toValue(view)] as const),
    queryFn: async () => pageItems(
      await client.get<PaginatedResponse<NotificationSummary>>(
        `/notifications?view=${toValue(view)}`,
      ),
    ),
    refetchInterval: 30_000,
    retry: false,
  })
}
