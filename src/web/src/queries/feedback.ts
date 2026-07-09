import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import type { ExecutionFeedback, ExecutionFeedbackInput } from '@/api/types'

export const feedbackQueryKey = (recommendationId: string | undefined, limit = 20) =>
  ['feedback', recommendationId ?? null, limit] as const

function invalidateFeedbackDependents(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: ['feedback'] })
  queryClient.invalidateQueries({ queryKey: ['notifications'] })
  queryClient.invalidateQueries({ queryKey: ['recommendations'] })
}

export function useFeedbackQuery(recommendationId?: string, limit = 20) {
  const client = useApiClient()
  return useQuery({
    queryKey: feedbackQueryKey(recommendationId, limit),
    queryFn: () => {
      const params = new URLSearchParams()
      if (recommendationId) {
        params.set('recommendation_id', recommendationId)
      }
      params.set('limit', String(limit))
      return client.get<ExecutionFeedback[]>(`/feedback?${params.toString()}`)
    },
  })
}

export function useRecordFeedbackMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: ExecutionFeedbackInput) => client.post<ExecutionFeedback>('/feedback', input),
    onSuccess: () => invalidateFeedbackDependents(queryClient),
  })
}
