// ratis_client/hooks/use-label-group-items.ts
//
// Lazy fetch of `GET /api/v1/scan/label-group?store_id=X&date=Y` — only
// accepted electronic_label scans are returned by the backend (unmatched +
// rejected are hidden, per ARCH_scan_history.md § Endpoints backend).

import { useQuery } from '@tanstack/react-query';
import { productClient } from '@/services/product-client';
import type { ConsensusState, ScanMatchMethod } from '@/hooks/use-receipt-items';

export interface LabelGroupItem {
  scan_id: string;
  product_name: string | null;
  product_ean: string | null;
  price_cents: number | null;
  match_method: ScanMatchMethod;
  scanned_at: string | null;
  /** NRC bloc E — see ``ConsensusState`` in ``use-receipt-items.ts``. */
  consensus_state?: ConsensusState | null;
}

export interface LabelGroupDetail {
  items: LabelGroupItem[];
}

export interface UseLabelGroupItemsOptions {
  /** Only true once the parent accordion expands — lazy-fetch pattern. */
  enabled: boolean;
}

export const labelGroupQueryKey = (storeId: string, date: string) =>
  ['label-group-items', storeId, date] as const;

export function useLabelGroupItems(
  storeId: string | null,
  date: string | null,
  { enabled }: UseLabelGroupItemsOptions,
) {
  return useQuery<LabelGroupDetail>({
    queryKey: labelGroupQueryKey(storeId ?? '__null__', date ?? '__null__'),
    queryFn: () =>
      productClient.get<LabelGroupDetail>(
        `/scan/label-group?store_id=${storeId}&date=${date}`,
      ),
    enabled: enabled && !!storeId && !!date,
  });
}
