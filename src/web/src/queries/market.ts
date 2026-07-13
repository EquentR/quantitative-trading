import { computed, type Ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import { fetchAllPages } from '@/api/pagination'
import type {
  DailyBarsResponse,
  IntradayStrengthResponse,
  MarketOverview,
  MarketCaptureRun,
  MarketSnapshotTrace,
  MarketSymbolSummary,
  MinuteBarsResponse,
  MoneyFlowResponse,
  PaginatedResponse,
} from '@/api/types'

const marketRefreshMs = 180_000

export const marketSymbolsQueryKey = ['market', 'symbols'] as const
export const marketRunsQueryKey = ['market', 'runs'] as const

export function useMarketRunsQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: marketRunsQueryKey,
    queryFn: () => client.get<PaginatedResponse<MarketCaptureRun>>(
      '/market/runs?page=1&page_size=20',
    ),
    refetchInterval: 30_000,
    retry: false,
  })
}

export function useMarketSymbolsQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: marketSymbolsQueryKey,
    queryFn: async () => {
      const items = await fetchAllPages<MarketSymbolSummary>(
        client,
        '/market/symbols',
        { pageSize: 250 },
      )
      return { items, total: items.length }
    },
    refetchInterval: marketRefreshMs,
    retry: false,
  })
}

function useSymbolQuery<T>(
  key: string,
  symbol: Ref<string | null>,
  path: (symbol: string) => string,
) {
  const client = useApiClient()
  return useQuery({
    queryKey: computed(() => ['market', key, symbol.value]),
    queryFn: () => client.get<T>(path(symbol.value!)),
    enabled: computed(() => Boolean(symbol.value)),
    refetchInterval: key === 'overview' || key === 'minute-bars' || key === 'intraday-strength'
      ? marketRefreshMs
      : false,
    retry: false,
  })
}

export function useMarketOverviewQuery(symbol: Ref<string | null>) {
  return useSymbolQuery<MarketOverview>('overview', symbol, (value) => `/market/symbols/${value}/overview`)
}

export function useDailyBarsQuery(symbol: Ref<string | null>) {
  return useSymbolQuery<DailyBarsResponse>(
    'daily-bars',
    symbol,
    (value) => `/market/symbols/${value}/daily-bars?limit=250`,
  )
}

export function useMoneyFlowQuery(symbol: Ref<string | null>) {
  return useSymbolQuery<MoneyFlowResponse>(
    'money-flow',
    symbol,
    (value) => `/market/symbols/${value}/money-flow?limit=60`,
  )
}

export function useMinuteBarsQuery(symbol: Ref<string | null>) {
  return useSymbolQuery<MinuteBarsResponse>(
    'minute-bars',
    symbol,
    (value) => `/market/symbols/${value}/minute-bars`,
  )
}

export function useIntradayStrengthQuery(symbol: Ref<string | null>) {
  return useSymbolQuery<IntradayStrengthResponse>(
    'intraday-strength',
    symbol,
    (value) => `/market/symbols/${value}/intraday-strength/latest`,
  )
}

export function useMarketTraceQuery(
  snapshotId: Ref<string | number | null>,
  symbol: Ref<string | null>,
) {
  const client = useApiClient()
  return useQuery({
    queryKey: computed(() => ['market', 'trace', snapshotId.value, symbol.value]),
    queryFn: () => client.get<MarketSnapshotTrace>(
      `/market/snapshots/${snapshotId.value}/trace?symbol=${encodeURIComponent(symbol.value!)}`,
    ),
    enabled: computed(() => snapshotId.value !== null && symbol.value !== null),
    retry: false,
  })
}
