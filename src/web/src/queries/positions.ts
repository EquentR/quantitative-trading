import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import type { Position, PositionInput } from '@/api/types'

export const positionsQueryKey = ['positions'] as const

interface SavePositionPayload {
  mode?: 'create' | 'update'
  position: PositionInput
}

function invalidatePositionDependents(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: positionsQueryKey })
  queryClient.invalidateQueries({ queryKey: ['account', 'snapshot'] })
  queryClient.invalidateQueries({ queryKey: ['service', 'status'] })
}

export function usePositionsQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: positionsQueryKey,
    queryFn: () => client.get<Position[]>('/positions'),
  })
}

export function useSavePositionMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ mode = 'create', position }: SavePositionPayload) => {
      if (mode === 'update') {
        return client.put<Position>(`/positions/${position.symbol}`, position)
      }
      return client.post<Position>('/positions', position)
    },
    onSuccess: () => invalidatePositionDependents(queryClient),
  })
}

export function useDeletePositionMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (symbol: string) => client.delete(`/positions/${symbol}`),
    onSuccess: () => invalidatePositionDependents(queryClient),
  })
}

export function useImportPositionsMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (positions: PositionInput[]) => client.post<Position[]>('/positions/import', { positions }),
    onSuccess: () => invalidatePositionDependents(queryClient),
  })
}

export function useImportPositionsCsvMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => client.uploadCsv<Position[]>('/positions/import-csv', file),
    onSuccess: () => invalidatePositionDependents(queryClient),
  })
}

export function downloadPositionsCsv() {
  return useApiClient().download('/positions/export-csv')
}
