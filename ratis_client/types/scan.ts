// ratis_client/types/scan.ts

export type ScanType = 'receipt' | 'label'
// 'unknown_store' → label scan saved but no store matched in radius.
// The user must scan a receipt to validate the store (Part B reconciliation).
export type ScanStatus =
  | 'uploading'
  | 'processing'
  | 'done'
  | 'error'
  | 'unknown_store'

export interface ScanItem {
  id: string
  type: ScanType
  status: ScanStatus
  createdAt: number
  // receipt — populated after processing
  storeName?: string
  totalCents?: number
  items?: { name: string; qty: number; priceCents: number }[]
  // label — populated after processing
  productName?: string
  priceCents?: number
  // used for polling
  backendScanId?: string   // receipt_id (receipt) or scan_id (label)
  sessionId?: string       // label only: session_id for GET /scan/label/session/{id}
}

export interface UploadQueueEntry {
  id: string               // shared with ScanItem.id
  type: ScanType
  photoUris: string[]      // always one URI per entry (one photo → one ScanItem)
  status: 'queued' | 'uploading' | 'done' | 'error'
  createdAt: number
  attempt: number          // max 3 before status → error
  // Stable client-generated UUID, set once at enqueue time and reused on
  // every upload attempt. Lets the backend dedup a replayed upload (app
  // killed after a successful POST but before recording success) instead
  // of creating a duplicate receipt. Receipt entries only.
  idempotencyKey?: string
  backendScanId?: string
  sessionId?: string
  // Label only: geo captured at shutter time — used by backend to resolve store.
  userLat?: number
  userLng?: number
}
