import { computed, toValue, type MaybeRefOrGetter, type Ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import { pageItems } from '@/api/pagination'
import type {
  PaginatedResponse,
  Recommendation,
  RecommendationListItem,
  RecommendationView,
} from '@/api/types'

export const recommendationsQueryKey = (view: RecommendationView) => ['recommendations', view] as const
export const recommendationQueryKey = (recommendationId: string) => ['recommendations', recommendationId] as const

export function useRecommendationsQuery(
  view: MaybeRefOrGetter<RecommendationView> = 'current',
) {
  const client = useApiClient()
  return useQuery({
    queryKey: computed(() => recommendationsQueryKey(toValue(view))),
    queryFn: async () => pageItems(
      await client.get<PaginatedResponse<RecommendationListItem>>(
        `/recommendations?view=${toValue(view)}`,
      ),
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
