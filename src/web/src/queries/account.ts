import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import type { AccountSnapshot, CreatedSnapshotResponse } from '@/api/types'

export const latestSnapshotQueryKey = ['account', 'snapshot', 'latest'] as const

export function useLatestSnapshotQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: latestSnapshotQueryKey,
    queryFn: () => client.get<AccountSnapshot>('/account/snapshots/latest'),
    retry: false,
  })
}

export function useCreateSnapshotMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => client.post<CreatedSnapshotResponse>('/account/snapshots'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: latestSnapshotQueryKey })
      queryClient.invalidateQueries({ queryKey: ['service', 'status'] })
    },
  })
}
