import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import type { CashAccount, CashTransaction } from '@/api/types'

export const cashAccountQueryKey = ['cash', 'account'] as const
export const cashTransactionsQueryKey = ['cash', 'transactions'] as const

interface InitializeCashPayload {
  cash: number
  note: string
}

export interface CashTransferPayload {
  type: 'transfer_in' | 'transfer_out'
  amount: number
  note: string
}

interface CashAdjustmentPayload {
  cash: number
  note: string
}

function invalidateCashDependents(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: cashAccountQueryKey })
  queryClient.invalidateQueries({ queryKey: cashTransactionsQueryKey })
  queryClient.invalidateQueries({ queryKey: ['account', 'snapshot'] })
  queryClient.invalidateQueries({ queryKey: ['service', 'status'] })
}

export function useCashAccountQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: cashAccountQueryKey,
    queryFn: () => client.get<CashAccount>('/cash/account'),
    retry: false,
  })
}

export function useCashTransactionsQuery(limit = 20) {
  const client = useApiClient()
  return useQuery({
    queryKey: [...cashTransactionsQueryKey, limit],
    queryFn: () => client.get<CashTransaction[]>(`/cash/transactions?limit=${limit}`),
  })
}

export function useInitializeCashAccountMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: InitializeCashPayload) => client.post<CashAccount>('/cash/account', payload),
    onSuccess: () => invalidateCashDependents(queryClient),
  })
}

export function useCashTransferMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: CashTransferPayload) => client.post<CashAccount>('/cash/transfers', payload),
    onSuccess: () => invalidateCashDependents(queryClient),
  })
}

export function useCashAdjustmentMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: CashAdjustmentPayload) => client.post<CashAccount>('/cash/adjustments', payload),
    onSuccess: () => invalidateCashDependents(queryClient),
  })
}
