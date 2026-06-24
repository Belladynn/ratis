// ratis_client/services/scan-events.ts
//
// Minimal event bus used by scan-queue.ts to notify the Scan UI after each
// background upload. The queue runs fire-and-forget; React screens subscribe
// via useEffect to react to completed batches (e.g. surface an
// "unknown store" modal).
//
// Kept intentionally tiny — no external deps, no persistence, no async.

export type BatchStoreStatus = 'confirmed' | 'pending' | 'unknown'

export type ScanEvent =
  | { type: 'batch_uploaded'; store_status: BatchStoreStatus }

type Listener = (e: ScanEvent) => void

const listeners = new Set<Listener>()

export const scanEvents = {
  emit(event: ScanEvent): void {
    // Iterate over a snapshot so listeners can unsubscribe during dispatch.
    for (const listener of Array.from(listeners)) {
      try {
        listener(event)
      } catch {
        // A broken listener must not stop other listeners — fire-and-forget.
      }
    }
  },
  subscribe(listener: Listener): () => void {
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  },
  // @internal — test-only reset
  _clearAll(): void {
    listeners.clear()
  },
}
