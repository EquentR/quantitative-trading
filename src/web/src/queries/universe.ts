import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import type { CreatedUniverseSnapshotResponse, UniverseMember, UniverseSnapshot } from '@/api/types'

export const universeQueryKey = ['universe', 'members'] as const
export const universeLatestSnapshotQueryKey = ['universe', 'snapshots', 'latest'] as const

function invalidateUniverseDependents(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: ['universe'] })
  queryClient.invalidateQueries({ queryKey: ['plans'] })
  queryClient.invalidateQueries({ queryKey: ['recommendations'] })
}

export function useUniverseQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: universeQueryKey,
    queryFn: () => client.get<UniverseMember[]>('/universe'),
  })
}

export function useLatestUniverseSnapshotQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: universeLatestSnapshotQueryKey,
    queryFn: () => client.get<UniverseSnapshot>('/universe/snapshots/latest'),
  })
}

export function useCreateUniverseSnapshotMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => client.post<CreatedUniverseSnapshotResponse>('/universe/snapshots'),
    onSuccess: () => invalidateUniverseDependents(queryClient),
  })
}
