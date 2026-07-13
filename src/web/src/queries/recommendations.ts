import { computed, type Ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import { pageItems } from '@/api/pagination'
import type { PaginatedResponse, Recommendation } from '@/api/types'

export const recommendationsQueryKey = ['recommendations'] as const
export const recommendationQueryKey = (recommendationId: string) => ['recommendations', recommendationId] as const

export function useRecommendationsQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: recommendationsQueryKey,
    queryFn: async () => pageItems(
      await client.get<PaginatedResponse<Recommendation> | Recommendation[]>('/recommendations'),
    ),
  })
}

export function useRecommendationQuery(recommendationId: Ref<string | null>) {
  const client = useApiClient()
  return useQuery({
    queryKey: computed(() => recommendationQueryKey(recommendationId.value ?? 'none')),
    queryFn: () => client.get<Recommendation>(`/recommendations/${recommendationId.value}`),
    enabled: computed(() => recommendationId.value !== null),
  })
}
