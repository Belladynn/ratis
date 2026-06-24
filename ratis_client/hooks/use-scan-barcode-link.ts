// ratis_client/hooks/use-scan-barcode-link.ts
//
// Mutation wrapping `POST /api/v1/scan/barcode` — links an EAN to an unmatched
// or ambiguous scan row. Used by the scan-history item rows when the user
// scans a barcode to resolve a 🔴/🟠 item.
//
// On success the receipt-items cache is invalidated so the UI updates the row
// color immediately. Errors (409 / 404) are forwarded to the caller for
// contextual toast messaging — see ARCH_scan_history.md § Flows · Flow B.

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { productClient } from '@/services/product-client';
import { receiptItemsQueryKey } from '@/hooks/use-receipt-items';

export interface ScanBarcodeLinkVariables {
  ean: string;
  scan_id: string;
}

export function useScanBarcodeLink(receiptId: string) {
  const qc = useQueryClient();
  return useMutation<unknown, Error, ScanBarcodeLinkVariables>({
    mutationFn: (vars) => productClient.post('/scan/barcode', vars),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: receiptItemsQueryKey(receiptId) });
    },
  });
}
