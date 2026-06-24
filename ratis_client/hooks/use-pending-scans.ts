// ratis_client/hooks/use-pending-scans.ts
//
// Reads the local AsyncStorage scan history (= queue + post-upload state)
// so the scan tab can surface uploading/processing/error entries that the
// backend `/scan/history` doesn't yet know about. Pairs with `useScanHistory`
// in the scan tab (AF-02 polish) — local pending entries take precedence
// over backend ones when ids overlap.

import { useQuery } from '@tanstack/react-query';
import { getHistory } from '@/services/scan-queue';
import type { ScanItem } from '@/types/scan';
import { useEffect } from 'react';
import { scanEvents } from '@/services/scan-events';

/**
 * Polls the local AsyncStorage scan history every 2s while the screen is
 * mounted (cheap — single read, no parsing of large payloads). Also
 * invalidates on `batch_uploaded` events so the user sees their scan
 * transition from `uploading` → `processing` immediately after enqueue.
 */
export function usePendingScans() {
  const query = useQuery<ScanItem[]>({
    queryKey: ['scan', 'local-history'],
    queryFn: getHistory,
    refetchInterval: 2_000,
    staleTime: 0,
  });

  useEffect(() => {
    const unsub = scanEvents.subscribe(() => {
      void query.refetch();
    });
    return unsub;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return query;
}
