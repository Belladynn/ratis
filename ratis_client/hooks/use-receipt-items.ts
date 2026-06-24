// ratis_client/hooks/use-receipt-items.ts
//
// Lazy fetch of `GET /api/v1/scan/receipt/{receipt_id}` — fires only when the
// caller (receipt accordion) flips `enabled=true` on first expand. Matches the
// extended backend shape (PR #93): `items[]` contains per-line scan detail.
//
// See `ratis_client/ARCH_scan_history.md` § Endpoints backend.

import { useQuery } from '@tanstack/react-query';
import { productClient } from '@/services/product-client';
import type { ScanStoreStatus } from '@/hooks/use-scan-history';

export type ReceiptStatus = 'pending' | 'processing' | 'done' | 'rejected' | 'failed';

/**
 * Pipeline-v3 (deployed 2026-04-30) replaces v2 `accepted/unmatched` with
 * `matched/unresolved`. Both v2 and v3 values appear in the type because rows
 * persisted before the rollout still hold the legacy values during the
 * transition window — see `utils/scan-status.ts` for the unified UX mapping.
 */
export type ReceiptItemStatus =
  | 'pending'
  | 'matched'
  | 'unresolved'
  | 'rejected'
  // legacy v2
  | 'accepted'
  | 'unmatched';

/**
 * Live consensus state for a scan, computed server-side from
 * `product_name_resolutions` (NRC). ``null`` ⇔ no contributing ledger row
 * (UNRESOLVED) ⇒ no badge in the UI. The five concrete states drive a
 * minimal status badge in `scan-history-item-row.tsx` — see
 * `webservices/ratis_product_analyser/repositories/consensus_state.py`.
 */
export type ConsensusState =
  | 'verified'
  | 'unverified'
  | 'controverse'
  | 'pending'
  | 'unresolved';

/**
 * `match_method` is free-form text from the backend to keep the client agnostic
 * of new matching strategies. Known v3 + v2 values drive the UI color mapping
 * (`utils/scan-status.ts`); unknown values fall back to a neutral green.
 */
export type ScanMatchMethod =
  // v3
  | 'barcode'
  | 'knowledge'
  | 'fuzzy_strict'
  | 'manual_admin'
  // legacy v2
  | 'barcode_ean'
  | 'manual'
  | 'fuzzy_confirmed'
  | 'fuzzy'
  | 'observed_name'
  | string
  | null;

export interface ReceiptItem {
  scan_id: string;
  scanned_name: string | null;
  product_name: string | null;
  /** Composed by the backend (`ratis_core.products.pick_display_name`) from
   *  the OFF multi-field columns (`product_name_fr`, `generic_name_fr`,
   *  `brands_text` + `quantity_text`). Prefer this over `product_name` for
   *  display — `product_name` remains in the response for backward-compat
   *  with older app versions and is identical when no enrichment exists. */
  display_name?: string | null;
  product_ean: string | null;
  quantity: number | null;
  price_cents: number | null;
  status: ReceiptItemStatus;
  match_method: ScanMatchMethod;
  /** Populated by the backend when `status` is `unresolved` or `rejected` —
   *  a snake_case code (e.g. `'fuzzy_below_threshold_0.65'`,
   *  `'no_fuzzy_candidate'`) translated by `formatRejectedReason()`. May be
   *  absent on legacy v2 rows. */
  rejected_reason?: string | null;
  /** NRC bloc E — live ConsensusState computed server-side. ``null`` (or
   *  absent on older backends) ⇒ no badge to render. */
  consensus_state?: ConsensusState | null;
}

/** OCR-parsed store header surfaced by the backend when a receipt's store is
 *  `unknown` or `pending` — drives the StoreConfirmationModal. The backend
 *  only emits this object when the candidate is rich enough to confirm
 *  (brand_guess + at least postal_code OR address). */
export interface ReceiptStoreCandidateInfo {
  brand_guess: string;
  address: string | null;
  postal_code: string | null;
  city: string | null;
  phone: string | null;
}

export interface ReceiptDetail {
  status: ReceiptStatus;
  matched: number;
  unmatched: number;
  total_amount: number | null;
  store_status: ScanStoreStatus | null;
  pending_items_count: number;
  items: ReceiptItem[];
  /** Present only when `store_status` is 'unknown' or 'pending' AND the OCR
   *  candidate is rich enough for the user to confirm — see
   *  ARCH_store_validation.md § Modification GET /scan/receipt/{id}. */
  store_candidate_info?: ReceiptStoreCandidateInfo | null;
}

export interface UseReceiptItemsOptions {
  /** Only true once the parent accordion expands — lazy-fetch pattern. */
  enabled: boolean;
}

/** React Query cache key for a single receipt — exported so consumers that
 *  mutate a child scan (barcode-link) can `invalidateQueries({queryKey: ...})`. */
export const receiptItemsQueryKey = (receiptId: string) => ['receipt-items', receiptId] as const;

export function useReceiptItems(receiptId: string | null, { enabled }: UseReceiptItemsOptions) {
  return useQuery<ReceiptDetail>({
    queryKey: receiptItemsQueryKey(receiptId ?? '__null__'),
    queryFn: () => productClient.get<ReceiptDetail>(`/scan/receipt/${receiptId}`),
    enabled: enabled && !!receiptId,
  });
}
