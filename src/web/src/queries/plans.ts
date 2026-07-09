import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import type { CreatedPlanResponse, TradingPlan } from '@/api/types'

export const latestPlanQueryKey = ['plans', 'latest'] as const
export const planQueryKey = (planId: string) => ['plans', planId] as const

function invalidatePlanDependents(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: ['plans'] })
  queryClient.invalidateQueries({ queryKey: ['universe', 'snapshots'] })
  queryClient.invalidateQueries({ queryKey: ['recommendations'] })
  queryClient.invalidateQueries({ queryKey: ['service', 'status'] })
}

export function useLatestPlanQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: latestPlanQueryKey,
    queryFn: () => client.get<TradingPlan>('/plans/latest'),
  })
}

export function usePlanQuery(planId: string) {
  const client = useApiClient()
  return useQuery({
    queryKey: planQueryKey(planId),
    queryFn: () => client.get<TradingPlan>(`/plans/${planId}`),
  })
}

export function useGeneratePlanMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload?: { trading_day?: string }) => client.post<CreatedPlanResponse>('/plans', payload),
    onSuccess: () => invalidatePlanDependents(queryClient),
  })
}
