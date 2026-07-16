import { useMutation, useQueryClient } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import type { InstrumentPreview } from '@/api/types'
import { datasourceStatusQueryKey } from '@/queries/datasource'

export function useEastmoneyCandidatesMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => client.get<InstrumentPreview>('/instruments/eastmoney-candidates'),
    retry: false,
    onSettled: () => queryClient.invalidateQueries({ queryKey: datasourceStatusQueryKey }),
  })
}

export function useInstrumentSearchMutation() {
  const client = useApiClient()
  return useMutation({
    mutationFn: (query: string) =>
      client.get<InstrumentPreview>(`/instruments/search?q=${encodeURIComponent(query.trim())}`),
    retry: false,
  })
}
