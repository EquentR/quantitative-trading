import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import type { WatchPinnedInput, WatchPinnedItem } from '@/api/types'

export const watchlistPinnedQueryKey = ['watchlist', 'pinned'] as const

function invalidateWatchlistDependents(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: watchlistPinnedQueryKey })
  queryClient.invalidateQueries({ queryKey: ['universe'] })
  queryClient.invalidateQueries({ queryKey: ['plans'] })
  queryClient.invalidateQueries({ queryKey: ['recommendations'] })
  queryClient.invalidateQueries({ queryKey: ['service', 'status'] })
}

export function useWatchlistPinnedQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: watchlistPinnedQueryKey,
    queryFn: () => client.get<WatchPinnedItem[]>('/watchlist/pinned'),
  })
}

export function useCreatePinnedItemMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: WatchPinnedInput) => client.post<WatchPinnedItem>('/watchlist/pinned', input),
    onSuccess: () => invalidateWatchlistDependents(queryClient),
  })
}

export function useUpdatePinnedItemMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ symbol, input }: { symbol: string; input: WatchPinnedInput }) =>
      client.put<WatchPinnedItem>(`/watchlist/pinned/${symbol}`, input),
    onSuccess: () => invalidateWatchlistDependents(queryClient),
  })
}

export function useDeletePinnedItemMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (symbol: string) => client.delete(`/watchlist/pinned/${symbol}`),
    onSuccess: () => invalidateWatchlistDependents(queryClient),
  })
}

export function useImportPinnedItemsMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (items: WatchPinnedInput[]) => client.post<WatchPinnedItem[]>('/watchlist/pinned/import', { items }),
    onSuccess: () => invalidateWatchlistDependents(queryClient),
  })
}

export function useImportPinnedCsvMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => client.uploadCsv<WatchPinnedItem[]>('/watchlist/pinned/import-csv', file),
    onSuccess: () => invalidateWatchlistDependents(queryClient),
  })
}

export function useWatchlistExportCsv() {
  return useApiClient().download('/watchlist/pinned/export-csv')
}

export function useSyncPinnedItemsMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (items: WatchPinnedInput[]) => client.post<WatchPinnedItem[]>('/watchlist/pinned/sync', { items }),
    onSuccess: () => invalidateWatchlistDependents(queryClient),
  })
}
