import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import type { Recommendation, RecommendationScanResponse } from '@/api/types'

export const recommendationsQueryKey = ['recommendations'] as const
export const recommendationQueryKey = (recommendationId: string) => ['recommendations', recommendationId] as const

function invalidateRecommendationDependents(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: ['recommendations'] })
  queryClient.invalidateQueries({ queryKey: ['notifications'] })
  queryClient.invalidateQueries({ queryKey: ['audit'] })
  queryClient.invalidateQueries({ queryKey: ['service', 'status'] })
}

export function useRecommendationsQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: recommendationsQueryKey,
    queryFn: () => client.get<Recommendation[]>('/recommendations'),
  })
}

export function useRecommendationQuery(recommendationId: string) {
  const client = useApiClient()
  return useQuery({
    queryKey: recommendationQueryKey(recommendationId),
    queryFn: () => client.get<Recommendation>(`/recommendations/${recommendationId}`),
  })
}

export function useScanRecommendationsMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => client.post<RecommendationScanResponse>('/recommendations/scan'),
    onSuccess: () => invalidateRecommendationDependents(queryClient),
  })
}
