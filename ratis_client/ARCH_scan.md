---
type: sub-arch
service: ratis_client
parent: ARCH_CLIENT
related: [ARCH_PRODUCT_ANALYSER, ARCH_scan_history]
status: production
tags: [scan, camera, receipt, label, upload-queue, client]
updated: 2026-04-24
---

# ratis_client — ARCH Scan Screen

> Fullscreen camera scan screen with glass overlays: history top-left, photo counter + Send top-right, mode switch (receipt/label) + gold capture button bottom. Real upload (no more mock): receipt → POST /receipt, label batch → POST /label/batch (with geo).
> @tags: scan camera receipt label upload-queue client takePictureAsync fullscreen overlays glass theme-v2 store-detection-backend
> @status: LIVRÉ V0
> @subs: auto

> Parent : [[ARCH_CLIENT]] · Relations : [[ARCH_PRODUCT_ANALYSER]], [[ARCH_scan_history]]

> Status: complete
> Branch: `feature/shopping-list`
> Updated 2026-04-21 — real upload flow (no more mock). The camera actually captures via `takePictureAsync`, and `store_id` is no longer sent to the backend:
>
> - **Receipts**: `POST /scan/receipt` takes only the image. The backend resolves the store via receipt barcode detection (DA-18) during the OCR pipeline.
> - **Labels**: `POST /scan/label/batch` takes `user_lat` + `user_lng`. The backend geo-matches to the nearest store within `search_radius_km` (user preference).
>
> Updated 2026-04-20 — theme v2 migration (PR #52). The Scan screen is fully rebuilt: fullscreen camera with glass overlays (history top-left, photo counter + Send top-right, mode switch + gold capture bottom). The legacy components `CameraPhase`, `ReviewPhase`, `ScanHistory` are removed and replaced by the components listed below. `barcode` mode is removed — the "scan barcode to check off an item" feature has been moved to the List screen.

---

## Implementation Checklist

**Base checklist:**
- [x] Local types defined (`types/scan.ts`)
- [x] Upload queue service (`services/scan-queue.ts`) — TDD before code
- [x] Background task registered (`expo-task-manager` + `expo-background-fetch`)
- [x] `scan-camera-view.tsx` — fullscreen `<CameraView>` + coral viewfinder corners (ex-`CameraPhase`)
- [x] `scan-history-overlay.tsx` — glass-card top-left with last 3 scans + "See all →" (replaces `ScanHistory`)
- [x] `scan-top-actions.tsx` — coral photo counter (Label mode) + royal-violet Send button (replaces `ReviewPhase`)
- [x] `scan-mode-switch.tsx` — Receipt/Label pill toggle (active in royal violet)
- [x] `scan-capture-button.tsx` — 66px round button gold gradient + white border
- [x] `scan.tsx` — overlay orchestration, polling `processing` items
- [x] Tests written (TDD — before code)
- [x] `expo-camera` and `expo-task-manager` mocks in jest.config.js if missing
- [x] `ruff check --fix` n/a — `eslint` / TypeScript clean
- [x] CI pipeline green

**Custom checklist:**
- [x] Upload queue survives app kill (AsyncStorage persist)
- [x] Label batches: 10 photos max per POST
- [x] Polling `GET /scan/receipt/{id}` and `GET /scan/label/session/{id}` — stops when no more `processing` items
- [x] Mode switch disabled once ≥ 1 label photo captured
- [x] No `AppHeader` or `PageTitleBand` on this screen — fullscreen camera under the overlays

> ⚠️ One item at a time. Do not move on to the next before finishing the current one.

---

## Index

- [Context](#context) [L.40 - L.53]
- [Local types](#local-types) [L.55 - L.95]
- [Consumed endpoints](#consumed-endpoints) [L.97 - L.115]
- [Screen structure](#screen-structure-no-more-phases--camera-always-active) [L.117 - L.167]
- [Upload queue](#upload-queue) [L.169 - L.210]
- [Scan history](#scan-history) [L.212 - L.240]
- [Rules](#rules) [L.242 - L.252]
- [Out of scope](#out-of-scope) [L.254 - L.260]

---

## Context

Read before starting:
- `CLAUDE.md`
- `KNOWN_PROBLEMS_INDEX.md`
- `DECISIONS_ACTED.md`
- `ratis_client/ARCH_liste.md` if it exists (same stack, same patterns)
- `webservices/ratis_product_analyser/ARCH.md` — POST and GET endpoints consumed here

Required dependencies:
- `expo-camera` — already installed (used in `ProductSearchBar`)
- `@react-native-async-storage/async-storage` — already installed
- `expo-task-manager` — install if missing
- `expo-background-fetch` — install if missing

Reference spec: `docs/superpowers/specs/2026-04-20-screens-theme-v2-design.md` (replaces the old spec `docs/superpowers/specs/_archive/2026-04/2026-04-18-scan-screen-design.md`)

---

## Local types

```ts
// ratis_client/types/scan.ts

type ScanType = 'receipt' | 'label'
type ScanStatus = 'uploading' | 'processing' | 'done' | 'error'

interface ScanItem {
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
}

interface UploadQueueEntry {
  id: string                  // shared with ScanItem.id
  type: ScanType
  photoUris: string[]         // local file URIs written by expo-camera
  status: 'queued' | 'uploading' | 'done' | 'error'
  createdAt: number
  attempt: number             // max 3 before status → error
  backendScanId?: string      // assigned by backend after upload, used for polling
}
```

AsyncStorage keys:
- `scan_upload_queue` — `UploadQueueEntry[]`
- `scan_history` — `ScanItem[]`

---

## Consumed endpoints

Source: `webservices/ratis_product_analyser/ARCH.md`

| Method | URL | Payload | Usage |
|---|---|---|---|
| POST | `/api/v1/scan/receipt` | `image` | Upload receipt → `202` + `receipt_id`. Store resolved backend-side (barcode DA-18). |
| POST | `/api/v1/scan/label/batch` | `images[]` + `user_lat` + `user_lng` + `hint` | Upload ≤10 label photos → `202` + `session_id` + `[scan_ids]`. Store resolved by geo-match. |
| GET | `/api/v1/scan/receipt/{receipt_id}` | — | Poll receipt status: `pending/processing/done/failed` + summary |
| GET | `/api/v1/scan/label/session/{session_id}` | — | Poll label batch status: `pending/processing/done/failed` + summary |

Auth: JWT (`Authorization: Bearer <token>`). Base URL: `EXPO_PUBLIC_PRODUCT_API_URL` (service `ratis_product_analyser`, **not** `ratis_auth`).

**Label batch error cases:**

- No store within `search_radius_km` → `404 no_store_in_radius`. On the client side, the entry becomes `status='error'` after 3 attempts.
- Geolocation not granted → capture is blocked in UI (button disabled + banner). An upload without `userLat`/`userLng` in the queue is marked `error` without a network call.

Backend rate limit: 3 req/min per endpoint — polling every 10s does not exceed it.

---

## Screen structure (no more phases — camera always active)

Theme v2: a single screen, fullscreen camera always active, glass panels as overlays.

```
┌─────────────────────────────────────┐
│  ┌ History ──┐    ┌ 📸 3/50 ┐     │
│  │ ● Milk...  │    └─────────┘     │  ← top overlay
│  │ ● Bread... │    ┌ SEND →───┐   │
│  │ ● Bananas  │    └──────────┘   │
│  │ See all →  │                    │
│  └────────────┘                    │
│                                     │
│         [viewfinder cam]             │
│     ┌─╮           ╭─┐               │
│     │ │  4 coral glow corners        │
│     ╰─┘           └─╯               │
│                                     │
│  ┌ 🧾 Receipt  |  🏷️ Label ┐      │  ← bottom mode switch
│  └──────────────────────────┘   │
│            ●  gold capture          │
├─────────────────────────────────────┤
│ <TabBar />                          │
└─────────────────────────────────────┘
```

### `ScanCameraView`

Props:
```ts
interface ScanCameraViewProps {
  mode: 'receipt' | 'label'
  onCapture: (uri: string) => void
}
```

Wrapper around `<CameraView>` from `expo-camera`. The viewfinder (4 coral glow corners) is always visible at the center.

### `ScanHistoryOverlay`

Props: `{ items: ScanItem[]; onSeeAll: () => void }`

Glass card (12-14 rgba bg + blur) top-left, max 3 summarized items (`● <Product name or receipt>`), royal-violet text CTA `See all →` at the bottom. Scroll locked — this is a non-interactive preview (except the CTA).

### `ScanTopActions`

Props:
```ts
interface ScanTopActionsProps {
  mode: 'receipt' | 'label'
  photoCount: number
  onSend: () => void
}
```

Top-right:
- `label` mode only: `📸 N/50` counter in coral
- `SEND →` button in royal violet, active as soon as `photoCount >= 1`
- `receipt` mode: these elements are hidden (send is immediate upon capture)

### `ScanModeSwitch`

Props: `{ mode: 'receipt' | 'label'; onChange: (m) => void; disabled: boolean }`

Bottom-center pill toggle:
```
[ 🧾 Receipt  |  🏷️ Label ]
```

Active in royal violet, violet border, white text. Inactive: transparent + muted text. `disabled=true` as soon as `photoCount >= 1` in label mode.

### `ScanCaptureButton`

Props: `{ onPress: () => void }`

66px round centered bottom button, gold gradient (`#FFB800` → darker), 3px outer white border, haptic feedback on press.

### Receipt vs label flow

- **Receipt**: tap capture → `onReceiptCaptured(uri)` → enqueue directly (no more `ReviewPhase`, sent without a dedicated review — review is left to the backend)
- **Label**: tap capture → accumulates in `capturedUris` → tap `SEND` → `onLabelsDone(uris)`

The `✕` cancel button is replaced by simply switching tabs (the user exits the scan via the TabBar).

---

## Upload queue

Service: `services/scan-queue.ts`

### Enqueue

```ts
enqueueReceipt(photoUri: string): Promise<string>
enqueueLabel(photoUri: string, lat: number, lng: number): Promise<string>
```

The lat/lng is captured at trigger time (`expo-location`) and
persisted with the queue entry (`userLat`/`userLng` in `UploadQueueEntry`).
It is then sent in the batch `FormData` (keys `user_lat`, `user_lng`).
Geo is never logged on the backend (GDPR).

Both functions:
1. Create an `UploadQueueEntry` with `status: 'queued'`
2. Create a corresponding `ScanItem` with `status: 'uploading'`
3. Persist both to AsyncStorage
4. Call `processQueue()` fire-and-forget

### processQueue

```
1. Read scan_upload_queue
2. Take entries with status='queued' ordered by createdAt
3. For each entry:
   a. Set status → 'uploading'
   b. POST to the appropriate endpoint (receipt or label/batch, 10 photos max)
   c. Success → backendScanId = response.id, status → 'done'
              → ScanItem status → 'processing'
   d. Error → attempt++
              → if attempt >= 3: status → 'error', ScanItem status → 'error'
              → otherwise: status → 'queued' (will be retried)
4. Persist changes to AsyncStorage
```

### Background task

```ts
// Registered at app startup (in scan.tsx or _layout.tsx)
TaskManager.defineTask(SCAN_QUEUE_PROCESSOR, processQueue)
BackgroundFetch.registerTaskAsync(SCAN_QUEUE_PROCESSOR, {
  minimumInterval: 60,   // iOS will ignore if < 15 min in practice
  stopOnTerminate: false,
  startOnBoot: true,
})
```

iOS limit: actual interval ~15 min minimum, OS constraint documented in `DECISIONS_PENDING.md`.

---

## Scan history

Theme v2: the in-screen history is limited to a compact preview (last 3 items via `ScanHistoryOverlay`). The full history lives in a dedicated screen `/scan/history` accessible via the `See all →` CTA.

### `ScanHistoryOverlay` preview (in-screen)

Props: `{ items: ScanItem[]; onSeeAll: () => void }`

Displays 3 items max, as dots + short label:
- `● Semi-skimmed milk — 1.25€` (label)
- `● Monoprix receipt — 18.40€` (receipt done)
- `● Analysis in progress…` (processing)

No accordion here, no fine distinction uploading/error: just the main label rendered according to `status` (errors displayed in red). Full detail is in the `/scan/history` screen.

### `/scan/history` screen (out of scope V1)

Placeholder route for V1 — full implementation in V2. This is where the former `ScanHistory` will live (receipt accordions, status badges, pull-to-refresh, filters). Rendered per item by `status`:

| status | type=receipt | type=label |
|---|---|---|
| `uploading` | Skeleton + blue progress bar | Same |
| `processing` | "Receipt sent · Analysis in progress…" · ⏳ badge | "Label sent · Analysis in progress…" · ⏳ badge |
| `done` | Accordion: store · N items · total — tap to expand items | Fixed row: name + price |
| `error` | "Unreadable receipt" · red ✗ badge | "Unreadable label" · red ✗ badge |

### Polling in `scan.tsx`

```ts
useFocusEffect(() => {
  const processingItems = items.filter(i => i.status === 'processing')
  if (processingItems.length === 0) return

  const interval = setInterval(() => pollProcessingItems(processingItems), 10_000)
  return () => clearInterval(interval)
})
```

`pollProcessingItems` calls `GET /scan/receipt/{id}` or `GET /scan/label/session/{id}` depending on type, updates `scan_history` in AsyncStorage if status changed.

---

## Rules

- Never show retry UI in V1 — `error` state is terminal for the user
- Never block UI during an upload — everything is async / fire-and-forget
- `[Done]` disabled if 0 label photos captured
- Mode switch disabled once ≥ 1 label photo captured
- Amounts always in cents in types, `formatCents()` for display
- Polling stopped as soon as all items are `done` or `error` (no unnecessary polling)
- Receipt images: 48h lifetime on backend (R2) — never reload the URI after that delay

---

## Unknown store — Part A

Added 2026-04-21 — fire-and-forget when geolocation does not match any store.

**Business rule**: a label batch outside a store radius is **never** rejected (no more 404). The backend persists the scans with `store_id=NULL`, `store_status='unknown'` and retains `user_lat/user_lng` for later reconciliation (Part B = receipt). **No CAB / XP** is awarded, the OCR worker is not even triggered.

**Frontend**:

- New module `services/scan-events.ts` — minimal event bus (`emit` / `subscribe`), zero dependencies.
- `processLabelBatch` reads `store_status` from the backend response and emits `{ type: 'batch_uploaded', store_status }` after each upload.
- `ScanScreen` subscribes to the bus and displays an **"Unknown store"** modal inviting the user to scan a receipt. Tap `Scan a receipt` → switch `mode='receipt'` + close modal. Tap `Later` → close modal.
- `ScanHistoryOverlay` displays an amber `⚠ Pending` badge (instead of the price) for each scan with `store_status='unknown'`.
- `ScanStatus` in `types/scan.ts` gains the value `'unknown_store'` to distinguish in local history.

**Endpoints**:

- `POST /scan/label/batch` responds **202** even in the absence of a store. Enriched response: `{ session_id, scan_ids, store_status: 'confirmed' | 'unknown' }`.
- `GET /scan/history` exposes `store_status` on each item (mirror of DB column `scans.store_status`).

**Part B (implemented — DA-30)**:

Flow (text diagram):

```
user scans label without store
         │
         ▼
 POST /scan/label/batch  ──▶  scans (store_status='unknown', user_lat/lng persisted)
         │
         ▼
user taps chip "Scan receipt →" (ScanHistoryOverlay)
         │                                 setMode('receipt') on scan.tsx
         ▼
 POST /scan/receipt  ──▶  OCR pipeline resolves store_id
         │                         │
         │                         ├─ known store (receipt barcode)
         │                         └─ unknown store
         │                              └─▶ geocoding.lookup(address)  (Nominatim)
         │                                  └─▶ store_creation_service  (dedup 50m same-brand)
         ▼
 reconciliation_service
   └─ match unknown scans (user_id, ≤7d, ≤100m store coords)
   └─ UPDATE scans: store_id, store_status='confirmed', user_lat/lng = NULL
   └─ notify_scan_accepted(scan_id) per scan
   └─ INSERT notification_outbox type='store_validated'
         │
         ▼
 worker outbox ratis_rewards  ──▶  push "Store X validated, +N CAB"
```

Retention: `unknown` scans not reconciled are hard-deleted by `batch_purge.purge_unknown_scans` after 7 days; ISO-week counters retained in `unknown_scans_weekly_aggregate`. PII `user_lat/user_lng` never persists more than 7 days.

Frontend components affected: `scan-history-overlay.tsx` (tappable chip instead of a badge) + `scan.tsx` (callback `onRequestReceiptMode={() => setMode('receipt')}`).

---

## Out of scope

- Retry button on error items
- Counter badge on tab icon
- Pull-to-refresh for history
- Push notification when a batch is processed
- Price challenges (see `ratis_product_analyser/ARCH.md`)
