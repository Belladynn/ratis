// ratis_client/hooks/use-scan-history.ts
//
// Unified scan history — backend shape since PR #93 :
//
//   GET /api/v1/scan/history?limit=<n>&cursor=<opaque>
//   → { entries: ScanHistoryEntry[], next_cursor: string | null }
//
// Entries are a discriminated union on `type`:
//   - "receipt"       → one per receipt_id
//   - "label_group"   → one per (store_id, DATE(scanned_at))
//
// Cursor is an opaque base64 string produced by the backend — we pass it back
// verbatim, URL-encoded to be safe with characters like `/` and `+`.
// See `ratis_client/ARCH_scan_history.md` § Hooks API.

import { useInfiniteQuery, type InfiniteData } from '@tanstack/react-query';
import { productClient } from '@/services/product-client';

// Mirrors backend scans.store_status — receipts only, label groups don't carry it.
export type ScanStoreStatus = 'confirmed' | 'pending' | 'unknown';

export interface ReceiptEntry {
  type: 'receipt';
  receipt_id: string;
  scanned_at: string | null;
  store_name: string | null;
  store_status: ScanStoreStatus | null;
  total_amount_cents: number | null;
  matched_count: number;
  unmatched_count: number;
  pending_count: number;
}

export interface LabelGroupEntry {
  type: 'label_group';
  group_key: string;
  store_id: string;
  date: string; // YYYY-MM-DD
  store_name: string | null;
  latest_scanned_at: string | null;
  accepted_count: number;
}

export type ScanHistoryEntry = ReceiptEntry | LabelGroupEntry;

export interface ScanHistoryPage {
  entries: ScanHistoryEntry[];
  next_cursor: string | null;
}

/** Stable React Query key for the unified scan history. Exported so screens
 *  that mutate scan-related state (uploads, barcode-link, store confirmation)
 *  can invalidate it without re-deriving the key shape. */
export const SCAN_HISTORY_QUERY_KEY = ['scan-history'] as const;

export interface UseScanHistoryOptions {
  /** Forwarded to React Query — when set, the FIRST page is refetched every
   *  N ms while the screen is mounted. Use only when something on the screen
   *  truly needs a live view (e.g. a pending receipt being processed by the
   *  pipeline). Pass `false` (the default) to disable polling. */
  refetchInterval?: number | false;
}

/**
 * Paginated, cursor-based infinite query over the user's unified scan history.
 *
 * Consumers typically flatten `data.pages.flatMap(p => p.entries)` for rendering
 * and wire `fetchNextPage()` to a list-end sentinel to paginate further.
 *
 * `options.refetchInterval` is opt-in : the scan tab passes a small interval
 * while a pending scan is in-flight so the UI flips from "Traitement en cours"
 * to its final state without manual pull-to-refresh. Other consumers should
 * leave it disabled to keep the history a cheap read.
 */
export function useScanHistory(
  limit = 20,
  options: UseScanHistoryOptions = {},
) {
  const { refetchInterval = false } = options;
  return useInfiniteQuery<
    ScanHistoryPage, // TQueryFnData — one page from the backend
    Error,
    InfiniteData<ScanHistoryPage, string | null>, // TData — aggregated shape exposed to consumers
    readonly unknown[],
    string | null // TPageParam — the cursor type
  >({
    queryKey: [...SCAN_HISTORY_QUERY_KEY, limit],
    initialPageParam: null,
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams();
      params.set('limit', String(limit));
      if (pageParam) {
        params.set('cursor', pageParam);
      }
      return productClient.get<ScanHistoryPage>(`/scan/history?${params.toString()}`);
    },
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    refetchInterval,
  });
}
