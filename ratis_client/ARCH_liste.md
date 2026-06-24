---
type: sub-arch
service: ratis_client
parent: ARCH_CLIENT
related: [ARCH_LIST_OPTIMISER, ARCH_product]
status: in-progress
tags: [liste, shopping-list, route-optim, client, itinerary]
updated: 2026-04-24
---

# ratis_client — ARCH Shopping List Screen

> Shopping list screen: build → optimize → shop flow. Flat list wired to the backend, async optimisation + grouped multi-store view, scan-FAB to check off items. Theme v2 PR #52.
> @tags: liste shopping-list route-optim client itinerary build-optimize-shop scan-fab grouped-view theme-v2
> @status: EN-COURS
> @subs: auto

> Parent: [[ARCH_CLIENT]] · Relations: [[ARCH_LIST_OPTIMISER]], [[ARCH_product]]

> Updated 2026-04-21 — added the build → optimize → shop flow.
> Status: V1 — flat list wired to the backend, async optimisation with grouped view.
> Branch: `feature/liste-optimize`

---

## Implementation Checklist

**Theme v2 (PR #52 — merged):**

- [x] `components/liste/list-scan-fab.tsx` — round purple scan button (glyph ∥∥)
- [x] `components/liste/list-add-fab.tsx` — round neutral + Add button
- [x] `components/liste/list-actions.tsx` — [scan][add] row
- [x] `components/liste/store-card.tsx` — store card + checkable items
- [x] `app/(tabs)/liste.tsx` — Products/Itinerary tabs, mock data 2 stores
- [x] `__tests__/components/liste/*.test.tsx` — render tests for each sub-component

**Scan-to-check-off (this block):**

- [x] `services/list-client.ts` — list API client (`listClient`)
- [x] `hooks/use-scan-check.ts` — `useScanCheck(listId)` — `POST /lists/{id}/scan-check`
- [x] `components/liste/scan-check-modal.tsx` — camera modal + status feedback
- [x] `app/(tabs)/liste.tsx` — `scanModalVisible` state, wire `onPressScan`
- [x] TDD tests (hook 4 cases + modal + list screen)

**Build → optimize → shop flow (this block):**

- [x] `hooks/use-shopping-list-detail.ts` — `GET /lists/{id}` (flat items)
- [x] `hooks/use-active-route.ts` — `GET /lists/{id}/route` (polling every 2 s when `computing`)
- [x] `hooks/use-optimize-route.ts` — `POST /lists/{id}/optimize`
- [x] `hooks/use-list-items.ts` — `useAddItem` / `useToggleItem` / `useDeleteItem`
- [x] `components/liste/list-item-row.tsx` — flat row (checkbox + delete)
- [x] `components/liste/route-store-card.tsx` — store card (grouped view)
- [x] `components/liste/optimize-cta.tsx` — "Optimise my route" button
- [x] `components/liste/list-empty-state.tsx` — empty state
- [x] `app/(tabs)/liste.tsx` — adaptive screen (flat ↔ grouped)
- [x] `services/api-client.ts` — added `patch` method
- [x] `expo-location` — installed via `expo install`

**Itinerary tab (PR #85 — this block):**

- [x] `app/(tabs)/liste.tsx` — Itinerary tab replaces the placeholder
- [x] 4 route tab states: `route-empty-no-items`, `route-empty-no-route`, `route-computing`, `route-summary`
- [x] Stop-order label "Stop N" above each `RouteStoreCard` (via `store.order`)
- [x] Distance displayed in summary when `distance_km` is non-null
- [x] Teal Pressable hint on Products when route is ready (navigates to Itinerary)
- [x] TDD tests: 7 new + 1 modified (justified in SESSION_LOG.md)
- [x] i18n `liste.itineraire.*` + `liste.products_tab.route_ready_hint` + `liste.summary.distance`

**Map provider — decision history:**

- [x] PR #439 — `react-native-maps` iOS-only (V1)
- [x] PR #440 — lazy-require + null guard (transition)
- [x] PR #441 — switch to MapLibre Native + direct OSM tiles (Path B — RGPD-pure, zero Google)
- [x] PR #443 — "Open in Maps" deeplink + RGPD warning (`RouteStopCard`)
- [x] ~~PR #444 — back to `react-native-maps` + `PROVIDER_GOOGLE` (iOS+Android). PO decision 2026-05-14: real-time traffic + FR POI density > Google independence.~~ **REVOKED 2026-05-25**: the Google Cloud billing account could never be activated → Google Maps abandoned.
- [x] Revert PR #444 (2026-05-25) — back to **MapLibre Native** but with **MapTiler** tiles (free tier, EU host, simple client API key, zero billing) instead of public OSM servers (OSMF policy forbids app usage). Route calculation stays on OSRM (backend), only rendering changes. Key via `EXPO_PUBLIC_MAPTILER_KEY` (runtime JS, never committed — R17); `app.config.ts` removed (only existed to inject the Google key). RGPD re-documented in [[PRIVACY]] § Cartographie & Itinéraire (MapTiler EU instead of Google).

**TODO V2 (out of current scope):**

- [ ] ProductSearchBar modal for `onPressAdd`
- [ ] List templates
- [ ] "⋯" menu — rename / clear / delete list
- [ ] 🗺️ icon — open Google Maps with waypoints from the Itinerary tab (deep-link)
- [ ] Automatic re-optimisation after item mutation (user must tap again currently)

> ⚠️ One item at a time. Do not move to the next without finishing the current one.

---

## Index

- [Context](#context)
- [Context deps](#context-deps)
- [Screen structure](#screen-structure)
- [Components by section](#components-by-section)
- [API Hooks](#hooks-api)
- [Scan-to-check-off flow](#flow-scan-to-check-off)
- [Build → optimize → shop flow](#flow-build--optimize--shop)
- [Rules](#rules)
- [Out of scope V1](#out-of-scope-v1)

---

## Context

Read before starting:

- `CLAUDE.md`
- `KNOWN_PROBLEMS_INDEX.md`
- `DECISIONS_ACTED.md`
- `ratis_client/ARCH_design_system.md`
- `ratis_client/ARCH_scan.md` — camera+expo-camera patterns
- `ratis_client/ARCH_product.md` — same API hook patterns

Reference spec:

- `docs/superpowers/specs/2026-04-20-screens-theme-v2-design.md` — Liste section + "Scan to check off flow"

Backend endpoints (existing — `ratis_list_optimiser`):

- `GET /api/v1/lists` — user's list of shopping lists
- `GET /api/v1/lists/{list_id}` — detail + items
- `POST /api/v1/lists` — create a list
- `POST /api/v1/lists/{list_id}/items` — add item
- `PATCH /api/v1/lists/{list_id}/items/{item_id}` — update (checked, quantity)
- `DELETE /api/v1/lists/{list_id}/items/{item_id}` — delete item
- `POST /api/v1/lists/{list_id}/scan-check` — auto-check via scanned EAN
- `POST /api/v1/lists/{list_id}/optimize` — route calculation

---

## Context deps

**Shared components (already existing):**

- `@/components/ui/screen-background` — gradient background theme v2
- `@/components/ui/app-header` — header (season, CAB, missions)
- `@/components/ui/page-title-band` — title band + right-side icons
- `@/components/ui/screen-card` — rounded card (used by StoreCard)

**List components (`components/liste/` folder):**

- `ListScanFab` — purple scan FAB
- `ListAddFab` — neutral add FAB
- `ListActions` — wrapper row for the 2 FABs
- `StoreCard` — store card with items

**Reusable scan components:**

- `ScanViewfinder` (from `components/scan/`) — coral corner-frame — **not used for scan-check**: the scan-check modal has its own framing (horizontal barcode zone)

**Utils:**

- `getStoreAccent(storeName)` — accent colour per store (same helper as `store-card`)
- `utils/shopping-totals.ts` — `formatCents()` to display prices

**Hooks (existing):**

- `useMissions`, `useBattlepass`, `useCabBalance` — for AppHeader

**Hooks (to create in this block):**

- `useScanCheck(listId)` — `POST /lists/{id}/scan-check`

**V2 Hooks (out of scope):**

- `useShoppingLists` — `GET /lists`
- `useAddListItem` / `useUpdateListItem` / `useDeleteListItem`

**Services:**

- `listClient` (new — `services/list-client.ts`) — wrapping `createApiClient` on `EXPO_PUBLIC_LIST_API_URL` (fallback `http://localhost:8003/api/v1`)

---

## Screen structure

Mockup reference (spec):

```
[ SafeAreaView ]
  [ ScreenBackground ]
  [ AppHeader — saison / CAB / missions ]
  [ PageTitleBand — "Ma liste"  + icônes 🗺️ ⋯ ]
  [ ScrollView ]
    [ Segmented Produits / Itinéraire ]
    [ Tab Produits ]
      [ ListActions — ∥∥ Scanner l'article  |  ＋ Ajouter ]
      [ StoreCard × N ]
    [ Tab Itinéraire (V2 placeholder) ]
  [ ScanCheckModal — monté à la racine, visible: state ]
```

---

## Components by section

### `ListScanFab`

```tsx
interface Props {
  onPress: () => void;
}
```

46px round, gradient `#A78BFA → #7C3AED → #5B21B6`, glyph `∥∥`. `testID="list-scan-fab"`.

### `ListAddFab`

```tsx
interface Props {
  onPress: () => void;
}
```

46px round, neutral white/alpha background, glyph `＋`. `testID="list-add-fab"`.

### `ListActions`

```tsx
interface Props {
  onPressScan: () => void;
  onPressAdd: () => void;
}
```

`space-between` row containing the 2 FABs.

### `StoreCard`

```tsx
interface StoreItem {
  id: string;
  name: string;
  price: number; // euros (V1 mock) — V2 : centimes
  done: boolean;
}
interface Props {
  storeName: string;
  products: StoreItem[];
  onToggleProduct: (id: string) => void;
}
```

Card with colour accent per store (`getStoreAccent(storeName)`). Items checkable on tap.

### `ScanCheckModal`

```tsx
interface ScanCheckModalProps {
  visible: boolean;
  listId: string | null;
  onClose: () => void;
  onCheckedItem?: (item: ScanCheckItem) => void;
}
```

Full-screen modal:

- Header: close (×) + title "Scan an item"
- `CameraView` (expo-camera) with `barCodeScannerSettings={{ barCodeTypes: ['ean13', 'ean8', 'upc_a'] }}`
- Inline viewfinder (horizontal rectangle 60% × 20%)
- Bottom overlay: contextual feedback based on status
- Empty state if `listId == null`: "You have no active list"
- Camera permission denied: message + close button

Haptic feedback:

- `checked` → `Haptics.notificationAsync(Success)`
- `already_checked` → `Haptics.notificationAsync(Warning)`
- `not_in_list` known product → `Haptics.selectionAsync()`
- Error → `Haptics.notificationAsync(Error)`

---

## Hooks API

| Hook                        | Endpoint                             | Status               |
| --------------------------- | ------------------------------------ | -------------------- |
| `useShoppingLists()`        | `GET /lists`                         | V2                   |
| `useShoppingList(listId)`   | `GET /lists/{id}`                    | V2                   |
| `useAddListItem(listId)`    | `POST /lists/{id}/items`             | V2                   |
| `useUpdateListItem(listId)` | `PATCH /lists/{id}/items/{item_id}`  | V2                   |
| `useDeleteListItem(listId)` | `DELETE /lists/{id}/items/{item_id}` | V2                   |
| `useScanCheck(listId)`      | `POST /lists/{id}/scan-check`        | **V1 (this block)**  |
| `useOptimizeList(listId)`   | `POST /lists/{id}/optimize`          | V2                   |

### `useScanCheck(listId)`

```ts
export type ScanCheckStatus = "checked" | "already_checked" | "not_in_list";

export interface ScanCheckItem {
  id: string;
  product_ean: string;
  name: string;
  quantity: number;
  checked: boolean;
}

export interface ScanCheckResponse {
  status: ScanCheckStatus;
  item?: ScanCheckItem;
  product?: { ean: string; name: string } | null;
}

export function useScanCheck(
  listId: string | null,
): UseMutationResult<ScanCheckResponse, Error, { productEan: string }>;
```

- `POST /lists/{listId}/scan-check` with `{ product_ean }`
- `onSuccess` → invalidates `['lists', listId]` and `['list-items', listId]`
- If `listId == null` or `mutation.mutate()` called without a list → synchronous throw (safeguard)

---

## Flow scan-to-check-off

1. Tap `ListScanFab` → `setScanModalVisible(true)`
2. Modal opens → requests camera permission if not yet granted
3. `CameraView` active → `onBarcodeScanned(payload)` when a code is detected
4. Modal debounces (avoids double-scan) → calls `scanCheck.mutate({ productEan: payload.data })`
5. Based on `response.status`:
   - **`checked`** → haptic Success + overlay "✅ Item checked" + auto-close after 1500ms + callback `onCheckedItem(item)`
   - **`already_checked`** → haptic Warning + overlay "⚠️ Already checked" + stays open (new scan possible after 1500ms debounce)
   - **`not_in_list`**:
     - `product !== null` → haptic selection + overlay `Add "{product.name}" to the list?` + Confirm/Dismiss buttons (V2: confirm button branches to `useAddListItem` — V1: just closes the modal)
     - `product === null` → haptic Error + overlay "Unknown product" + debounce 1500ms
6. Network/API error → red overlay "Error" + stays open

---

## Flow build → optimize → shop

### Products / Route separation (since PR #85)

The screen has **2 tabs** with distinct responsibilities:

- **Products tab** = build + checklist (never the route view)
- **Itinerary tab** = exclusive route view (summary + ordered stores)

A visual hint `✓ Route ready — open the Itinerary tab` appears on Products when a route is ready, pressable to switch to Itinerary.

### Products Tab — 4 states

| State            | Trigger                            | UI                                                                                                                                               |
| ---------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Loading**      | `useShoppingListDetail.isLoading`  | Centred `ActivityIndicator`                                                                                                                      |
| **Empty**        | `detail.items.length === 0`        | `<ListEmptyState />`                                                                                                                             |
| **Flat (build)** | items present, no route ready      | `ListItemRow` × N + `<OptimizeCTA />` at the bottom                                                                                             |
| **Computing**    | `route.status === 'computing'`     | greyed-out flat list + "Optimisation in progress…" banner (no CTA)                                                                               |
| **Ready (hint)** | `route.status === 'ready'`         | flat list `ListItemRow` × N + teal Pressable hint "✓ Route ready — open the Itinerary tab" above (no optimize CTA since already optimised)       |

### Itinerary Tab — 4 states

| State         | Trigger                        | UI                                                                                                                                                                      |
| ------------- | ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **No items**  | `detail.items.length === 0`    | `testID=route-empty-no-items` — "Your list is empty" + hint                                                                                                             |
| **No route**  | items but no route             | `testID=route-empty-no-route` — "No itinerary yet" + `<OptimizeCTA />`                                                                                                  |
| **Computing** | `route.status === 'computing'` | `testID=route-computing` — spinner + "Optimisation in progress…"                                                                                                        |
| **Ready**     | `route.status === 'ready'`     | `testID=route-summary` — band (Total / Savings / Distance) + re-optimize `↻` + `RouteStoreCard` × N, each card preceded by a "Stop N" label (via `store.order`)        |

### Flow rules

1. The user builds their flat list on Products (scan, add, toggle, delete) — no stores.
2. Tap `<OptimizeCTA />` (from Products or Itinerary) → `Location.requestForegroundPermissionsAsync()` then `getCurrentPositionAsync()` → `POST /lists/{id}/optimize`.
3. Backend 202 `{id, status: "computing"}` → invalidate `['route', listId]`.
4. `useActiveRoute` switches to 2 s polling while `status === 'computing'`.
5. Worker finishes → `status: 'ready'` → Itinerary tab shows summary + ordered stores; Products tab shows the teal hint pointing to Itinerary.
6. Stores are NOT persisted on the list side — they live on the `OptimizedRoute` (TTL 24 h). Adding/deleting an item on the DB side automatically invalidates the route (re-optimise).
7. `checked` states stay on `shopping_list_items`. UI merge is done by `item_id`: `RouteStoreCard` receives `checkedItemIds: Set<string>` for strikethrough.
8. 404 on `/lists/{id}/route` = no route → rendered as `null` (normal state before the first optimisation).

**Errors handled on the frontend:**

| Backend code                     | UI                                                    |
| -------------------------------- | ----------------------------------------------------- |
| `empty_list` (422)               | error surfaced by the mutation (future toast)         |
| `no_position` (422)              | same — app always passes `lat`/`lng`                  |
| `cannot_optimize_template` (422) | same                                                  |
| `list_not_found` (404 initial)   | no call — `listId == null` disables the hook          |
| `no_active_route` (404)          | treated as `null` — not an error                      |

**Out of scope V1 (this block):**

- `move-item` / `remove-store` (manual route mutation)
- Automatic re-optimisation after item mutation (user must tap again)
- Itinerary (separate tab with map / timings) — stays V2 placeholder
- ProductSearchBar (manually adding an item)

---

## Rules

- **i18n** — every visible string via `t('key')` (preparation — V1 may use a local `STRINGS` object if `t` is not yet wired)
- **Destructive confirmation** — every item deletion goes through an `Alert.alert` (V2)
- **Haptic feedback** — mandatory on each scan result (success/warning/error)
- **Scan debounce** — only one active API call at a time, 1500ms cooldown between 2 scans
- **Camera permission** — if denied → message + close button (no crash)
- **`listId == null`** — modal shows empty state, no camera opening
- **testID**:
  - Root `<Modal>` → `scan-check-modal`
  - Close button → `scan-check-modal-close`
  - Feedback zone → `scan-check-feedback`
  - Empty state no-list → `scan-check-no-list`

---

## Out of scope V1

- Itinerary mode (optimised route display + timing)
- List templates (pre-filled recurring lists)
- ProductSearchBar (manually adding an item via search)
- All `useShoppingLists` / `useAddListItem` / etc. hooks — while the screen stays on mock data
- "⋯" menu (rename, clear, delete)
- 🗺️ icon (open route view)
- Swipe-to-delete on item
- Drag-to-reorder by store
- Multi-user list sharing
- Offline-first sync (action queue)

---

## Backend (reference)

`POST /api/v1/lists/{list_id}/scan-check` — `ratis_list_optimiser/routes/shopping_lists.py:314`

Body: `{ "product_ean": "3428270000019" }`

Response:

```json
{
  "status": "checked" | "already_checked" | "not_in_list",
  "item": { "id", "product_ean", "name", "quantity", "checked" } | undefined,
  "product": { "ean", "name" } | null
}
```

- `checked` → item was present and unchecked → now checked (DB mut)
- `already_checked` → item present and already checked → no-op
- `not_in_list` → product not in the list:
  - `product` non-null if the product exists in the `products` table
  - `product` null if EAN is unknown
