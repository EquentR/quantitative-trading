import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import type { DatasourceStatus } from '@/api/types'

export const datasourceStatusQueryKey = ['datasource', 'eastmoney', 'status'] as const

interface ApiKeyPayload {
  api_key: string
}

function invalidateDatasourceDependents(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: datasourceStatusQueryKey })
  queryClient.invalidateQueries({ queryKey: ['service', 'status'] })
}

export function useDatasourceStatusQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: datasourceStatusQueryKey,
    queryFn: () => client.get<DatasourceStatus>('/datasource/eastmoney/status'),
  })
}

export function useUpdateDatasourceKeyMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: ApiKeyPayload) => client.put<DatasourceStatus>('/datasource/eastmoney/key', payload),
    onSuccess: () => invalidateDatasourceDependents(queryClient),
  })
}

export function useDeleteDatasourceKeyMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => client.delete<DatasourceStatus>('/datasource/eastmoney/key'),
    onSuccess: () => invalidateDatasourceDependents(queryClient),
  })
}

export function useCheckDatasourceMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => client.post<DatasourceStatus>('/datasource/eastmoney/check'),
    onSuccess: () => invalidateDatasourceDependents(queryClient),
  })
}
