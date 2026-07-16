import { expect, test } from 'vitest'
import type { InstrumentMetadataFields, WatchPinnedItem } from '@/api/types'

type WatchPinnedMetadataContract = Omit<InstrumentMetadataFields, 'metadata_checked_at'> & {
  metadata_checked_at: string | null
}

const acceptCompleteMetadata = (_item: WatchPinnedMetadataContract) => undefined

test('watchlist items expose the complete backend instrument metadata contract', () => {
  const item = {} as WatchPinnedItem
  expect(acceptCompleteMetadata(item)).toBeUndefined()
})
