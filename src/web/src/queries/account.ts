import { useQuery } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import type { AccountSnapshot } from '@/api/types'

export const latestSnapshotQueryKey = ['account', 'snapshot', 'latest'] as const

export function useLatestSnapshotQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: latestSnapshotQueryKey,
    queryFn: () => client.get<AccountSnapshot>('/account/snapshots/latest'),
    retry: false,
  })
}
