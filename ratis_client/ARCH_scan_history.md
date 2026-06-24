---
type: sub-arch
service: ratis_client
parent: ARCH_CLIENT
related: [ARCH_scan, ARCH_PRODUCT_ANALYSER]
status: in-progress
tags: [scan-history, receipt, label, client, rescan]
updated: 2026-04-24
---

<!-- 2026-04-24 · V0 frontend implemented (feature/scan-history-v0-frontend).
     Implementation notes:
     - `scan-check-modal.tsx` is NOT removed: it still handles the
       scan-check flow in the list with contextual feedback (not_in_list, already_checked).
       The generic primitive `BarcodeScannerModal` is introduced as a
       fresh component in `components/scan/` and used by the
       scan-history screen. The full rename + migration of ScanCheckModal to
       BarcodeScannerModal is left as clean tech debt — tracked here.
     - New hook `useScanBarcodeLink(receiptId)` added on top of the
       Hooks API table below (mutation `POST /scan/barcode` with automatic
       cache invalidation of receipt-items). It was not explicitly listed
       in the checklist but is required for Flow B.
     - `useScanHistory` is now a `useInfiniteQuery<ScanHistoryPage>` —
       consumers read `data.pages.flatMap(p => p.entries)`. The
       opaque cursor is passed URL-encoded. The first page-param is `null`.
-->


# ratis_client — ARCH Scan History Screen

> Dedicated screen `/scan-history` accessible from `scan.tsx` ("See all →"). V0 display receipts/labels + barcode link + rescan. V0.1: per-image GPS fix for multi-store anti-fraud. `useInfiniteQuery` + `BarcodeScannerModal`.
> @tags: scan-history receipt label client rescan infinite-query barcode-scanner-modal v0 v0.1 gps-per-image anti-fraud
> @status: EN-COURS
> @subs: auto

> Parent: [[ARCH_CLIENT]] · Relations: [[ARCH_scan]], [[ARCH_PRODUCT_ANALYSER]]

> Created 2026-04-24 — dedicated screen `/scan-history` accessible from `scan.tsx` button "See all →".
> Covers V0 (display + barcode link + rescan) AND V0.1 (per-image GPS fix for multi-store anti-fraud).
> Status: V0 in progress.

---

## Implementation Checklist

**V0 — History screen (this PR, ~3h):**

### Backend
- [x] Extension `GET /api/v1/scan/receipt/{receipt_id}`: add `items: [{scan_id, scanned_name, product_name, product_ean, quantity, price_cents, status, match_method}]`
- [x] Rework `GET /api/v1/scan/history` — unified grouped return:
  - Entries type `receipt`: 1 per receipt_id (unchanged from external UX perspective)
  - Entries type `label_group`: 1 per `(store_id, DATE(scanned_at))` — SQL group
  - Automatic filtering: `status='rejected'` excluded regardless of `rejected_reason`
  - Order: most-recent-activity DESC, cursor paginated (keyset `(latest_activity_at, disambiguator)` base64-encoded)
- [x] New endpoint `GET /api/v1/scan/label-group?store_id=X&date=YYYY-MM-DD` → returns the list of **accepted** scans in the group (status='accepted' only, unmatched labels are hidden in the UI)
- [x] Backend tests: 1 per new behavior + 1 anti-regression test on classic `/scan/history`

> **Breaking change shape note (2026-04-24)** — the shape of `/scan/history` changes from `{items: [...]}` flat to `{entries: [{type: "receipt"|"label_group", ...}]}`. The frontend (`hooks/use-scan-history.ts`) must be reworked in the next PR — see `Frontend` section below. In the meantime, the current app will break on this endpoint. The cursor is now an opaque base64 string (no longer a UUID), so the old param `cursor=<uuid>` will return 422.

### Frontend
- [x] `hooks/use-receipt-items.ts` — `useReceiptItems(receiptId, {enabled})` lazy-fetch on receipt expand
- [x] `hooks/use-label-group-items.ts` — `useLabelGroupItems(storeId, date, {enabled})` lazy-fetch on group expand
- [x] Rework `useScanHistory` to consume the new unified shape (`useInfiniteQuery` + cursor pagination)
- [x] `app/scan-history.tsx` — full-page screen (FlatList + pull-to-refresh + infinite scroll)
- [x] `components/scan/scan-history-receipt-accordion.tsx` — receipt accordion with colored items
- [x] `components/scan/scan-history-label-accordion.tsx` — label group accordion
- [x] `components/scan/scan-history-item-row.tsx` — reusable item row (colored barcode button + name + price + quantity)
- [x] New `components/scan/barcode-scanner-modal.tsx` generic with `onBarcode: (ean) => Promise<void>` (deferred rename: scan-check-modal stays in place on the list side to avoid breaking its specific feedback logic — the generic primitive is in place and used by the new scan-history screen)
- [x] New `hooks/use-scan-barcode-link.ts` — mutation `POST /scan/barcode` + invalidation `['receipt-items', receipt_id]`
- [x] Wire in `scan.tsx`: "See all →" button → `router.push('/scan-history')` + projection of unified `entries[]` onto the compact overlay
- [x] i18n keys `scan.history.*` (FR only V0)
- [x] TDD tests: 17 cases (6 hooks + 6 item-row + 6 barcode-modal + 4 receipt-accordion + 3 label-accordion + 6 screen + 2 new on scan.tsx) — see Tests to cover section

**V0 PR-A — User-correction quick-wins FE-only (this PR, ~30min):**
- [x] `components/scan/scan-history-receipt-accordion.tsx` — display receipt date (`scanned_at`) next to store name, short FR format `DD MMM.` (e.g.: `27 avr.`)
- [x] `components/scan/scan-history-label-accordion.tsx` — same for `latest_scanned_at`
- [x] `utils/date.ts` — shared `formatScanDate(iso)` helper, hand-formatted (no `Intl.DateTimeFormat`) to remain safe on Android JSC without full ICU
- [x] Store pencil icon V0 (placeholder) — `components/scan/scan-history-receipt-accordion.tsx`, tappable `✎` icon on the right of the name, red if `store_status ∈ {pending,unknown}` or `store_name == null`, grey otherwise. Click → `Alert.alert("Coming soon", …)`. **No pencil icon on label_groups** (store-confirmed by construction)
- [x] i18n keys `scan.history.edit_store.coming_soon_{title,body}` added to `locales/fr.json`
- [x] TDD tests: 12 (4 helper `formatScanDate` + 6 receipt-accordion: 2 date + 3 pencil color + 1 click Alert + 2 label-accordion: 1 date + 1 no-pencil)
- [ ] PR-B (next): wire a backend endpoint `PATCH /api/v1/scan/receipt/{receipt_id}/store` + replace the Alert placeholder with a store picker + confirmation

**V0.1 — Per-image GPS fix (follow-up PR, ~2h):**
- [ ] Frontend: capture `{lat, lng, captured_at}` **at each photo** (not at send time)
- [ ] Backend: `BatchLabelRequest` Pydantic refactor — `images: [{photo, lat, lng, captured_at}]` instead of globals
- [ ] Service `label_service._upload_and_create_scan` — uses per-image coords
- [ ] Transparent migration: optional fields for backward compatibility (fallback = current shared GPS if new fields absent)
- [ ] Backend tests: label batch with distinct coords per image → scans created with distinct store_id (anti-fraud test)

---

## Index

- [Context](#context)
- [V0 · History screen](#v0--history-screen)
  - [Screen structure](#screen-structure)
  - [Components](#components)
  - [Hooks API](#hooks-api)
  - [Backend endpoints](#backend-endpoints)
  - [Mapping match_method → color](#mapping-match_method--color)
  - [Flows](#flows)
  - [i18n keys](#i18n-keys)
  - [Tests to cover](#tests-to-cover)
- [V0.1 · Per-image GPS fix](#v01--per-image-gps-fix)
- [Rules](#rules)
- [Out of scope](#out-of-scope)

---

## Context

**Read before starting:**
- `ClaudeV2.md` (rules R01 TDD, R02 db.commit, R03 layered arch, R22 tests fixture)
- `ratis_client/ARCH_scan.md` — camera + expo-camera patterns, scan-check flow to reuse
- `ratis_client/ARCH_liste.md` — adaptive screen pattern with accordion (RouteStoreCard)
- `ratis_client/ARCH_design_system.md` — theme v2 color palette

**Existing endpoints (reused or extended):**
- `GET /api/v1/scan/history` — to rework (unified grouped return)
- `GET /api/v1/scan/receipt/{receipt_id}` — to extend (add `items[]`)
- `POST /api/v1/scan/barcode` — reused as-is to link product-to-unmatched-scan_item
- `GET /api/v1/scan/label-group` — new

**Ratis models involved:**
- `scans` (each row = 1 scanned item, linked to `receipt_id` for receipts, independent for labels)
- `receipts` — aggregates the `scans` of a receipt
- `products` — matching target

---

## V0 · History screen

### Screen structure

```
[ SafeAreaView edges=['top'] ]
  [ ScreenBackground ]
  [ Header with back button + title "Historique" ]
  [ ScrollView (pull-to-refresh) ]
    [ Entry 1 — TICKET receipt accordion ]
    [ Entry 2 — LABEL group accordion ]
    [ Entry 3 — TICKET receipt accordion ]
    [ ... ]
    [ InfiniteScroll trigger (cursor-based) ]
    [ EmptyState if list is empty ]
```

**Entry types:**

### Type A · Receipt
```
┌───────────────────────────────────────────────────┐
│  🏪 Carrefour Ménilmontant                        │
│  12 articles · 47.35€  (10 reconnus · 2 à         │
│  compléter)                                       │
│  il y a 2h            [Rescanner] ▼               │
├───────────────────────────────────────────────────┤  ← collapsed hidden
│  Lait demi-écrémé 1L       ×1    1.29€    [🟢]   │
│   (ocr: LAIT DE DE-ECR)                           │
│  Nutella 400g              ×1    4.89€    [🟠]   │
│   Match incertain — vérifier                      │
│  PATE A TART FERRE         ×1    2.99€    [🔴]   │
│   Non reconnu — scanne le code-barre              │
│  *Article à identifier*    ×1    1.50€    [🔴]   │
│   On sait qu'il y avait un produit ici            │
│  ⏰ Traitement en cours…                          │
└───────────────────────────────────────────────────┘
```

### Type B · Label group (by store, by day)
```
┌───────────────────────────────────────────────────┐
│  🏪 Monoprix République                           │
│  8 produits pris en compte · il y a 3h      ▼    │
├───────────────────────────────────────────────────┤  ← collapsed hidden
│  Yaourt Danone nature      1.15€         [🟢]    │
│  Lait demi-écrémé 1L       1.29€         [🟢]    │
│  ...                                              │
│  (les échecs silencieux ne sont pas affichés)     │
└───────────────────────────────────────────────────┘
```

**Important note**: on the label side, only `status='accepted'` entries are displayed in the accordion. The header shows only the count of **accepted products** — failures are hidden so as not to make the user feel guilty about OCR misfires (fire-and-forget philosophy). Rejected entries (corrupted image, dedup, etc.) are hidden everywhere.

---

### Components

**`scan-history-receipt-accordion.tsx`**
```tsx
interface Props {
  entry: ReceiptEntry; // { receipt_id, store_name, scanned_at, total_amount_cents, matched_count, unmatched_count, pending_count }
}
```
- Tappable header (toggle expand)
- Lazy fetch `useReceiptItems(receipt_id)` on first expansion
- "Rescanner" button → `router.push('/(tabs)/scan')` (backend auto-supersede via `receipt_barcode`)
- Body: list of `scan-history-item-row.tsx`

**`scan-history-label-accordion.tsx`**
```tsx
interface Props {
  entry: LabelGroupEntry; // { store_id, date, store_name, accepted_count }
}
```
- Tappable header
- Lazy fetch `useLabelGroupItems(store_id, date)` on first expansion
- Body: list of `scan-history-item-row.tsx`
- **No "Rescanner"** (labels = fire-and-forget, failures are accepted)

**`scan-history-item-row.tsx`**
```tsx
interface ItemRow {
  scan_id: string;
  display_name: string;       // product_name if matched, scanned_name otherwise, "Article à identifier" if null
  scanned_name: string | null; // shown as subtitle if different from display_name
  quantity: number;
  price_cents: number | null;
  status: 'accepted' | 'unmatched' | 'pending' | 'rejected';
  match_method: string | null;
  product_ean: string | null;
}
```
- Row layout: name + subtitle + qty + price + colored barcode button
- Barcode button tap → opens `BarcodeScannerModal` with `onBarcode: (ean) => scanBarcode({ ean, scan_id })`
- If `status='pending'` → shows ⏰ instead of the button, non-tappable

**`barcode-scanner-modal.tsx` (refactored from `scan-check-modal.tsx`)**
```tsx
interface Props {
  visible: boolean;
  onClose: () => void;
  onBarcode: (ean: string) => Promise<void>; // caller's mutateAsync
  title?: string;
  feedbackText?: { success: string; error: string };
}
```
- Generic: the caller provides the handler. Reused for:
  - List scan-check (current): handler = `scanCheckMutation.mutateAsync({productEan})`
  - Receipt item link: handler = `scanBarcodeMutation.mutateAsync({ean, scan_id})`

---

### Hooks API

| Hook | Endpoint | Strategy |
|---|---|---|
| `useScanHistory(limit=20)` | `GET /scan/history?limit&cursor` | Infinite query, cursor pagination |
| `useReceiptItems(receiptId)` | `GET /scan/receipt/{id}` with extended items | Lazy — enabled=false until expand |
| `useLabelGroupItems(storeId, date)` | `GET /scan/label-group?store_id=X&date=Y` | Lazy same |
| `useScanBarcodeLink()` | `POST /scan/barcode` | Mutation, invalidates `['receipt-items', receipt_id]` on success |

---

### Backend endpoints

#### Extension: `GET /api/v1/scan/receipt/{receipt_id}`

**Before**:
```json
{
  "status": "done",
  "matched": 10,
  "unmatched": 2,
  "total_amount": 4735,
  "store_status": "confirmed",
  "pending_items_count": 0
}
```

**After** (adding the `items` field):
```json
{
  "status": "done",
  "matched": 10,
  "unmatched": 2,
  "total_amount": 4735,
  "store_status": "confirmed",
  "pending_items_count": 0,
  "items": [
    {
      "scan_id": "uuid",
      "scanned_name": "LAIT DE DE-ECR",
      "product_name": "Lait demi-écrémé 1L",
      "product_ean": "3428270000019",
      "quantity": 1,
      "price_cents": 129,
      "status": "accepted",
      "match_method": "barcode_ean"
    },
    {
      "scan_id": "uuid",
      "scanned_name": null,
      "product_name": null,
      "product_ean": null,
      "quantity": 1,
      "price_cents": 150,
      "status": "unmatched",
      "match_method": null
    }
  ]
}
```
**Item ordering**: `ORDER BY scanned_at ASC, id ASC` (order of appearance on the receipt).
**Filtering**: exclude `status='rejected'` (superseded, dup, etc.).

#### Rework: `GET /api/v1/scan/history`

**New returned shape** — unified paginated list:
```json
{
  "entries": [
    {
      "type": "receipt",
      "receipt_id": "uuid",
      "scanned_at": "2026-04-24T10:00:00Z",
      "store_name": "Carrefour Ménilmontant",
      "store_status": "confirmed",
      "total_amount_cents": 4735,
      "matched_count": 10,
      "unmatched_count": 2,
      "pending_count": 0
    },
    {
      "type": "label_group",
      "group_key": "uuid-store|2026-04-24",
      "store_id": "uuid-store",
      "date": "2026-04-24",
      "store_name": "Monoprix République",
      "latest_scanned_at": "2026-04-24T09:30:00Z",
      "accepted_count": 8
    }
  ],
  "next_cursor": "opaque-cursor-string" 
}
```

**SQL for label_group**:
```sql
SELECT store_id, DATE(scanned_at AT TIME ZONE 'UTC') AS day,
       MAX(scanned_at) AS latest_scanned_at,
       COUNT(*) FILTER (WHERE status='accepted') AS accepted_count
FROM scans
WHERE user_id = :user_id
  AND scan_type = 'electronic_label'
  AND status != 'rejected'
GROUP BY store_id, day
HAVING COUNT(*) FILTER (WHERE status='accepted') > 0  -- do not show a 0/N group
```

**Final entries ordering**: by `latest_activity_at` DESC (= `scanned_at` for receipts, `MAX(scanned_at)` for groups).
**Cursor**: opaque base64-encoded tuple `(latest_activity_at, entry_disambiguator)` where `entry_disambiguator = receipt_id` or `group_key` — stable keyset for pagination. Decoded server-side, not exposed raw to the client.

**Receipt header counters**:
- Main header line: `{matched_count + unmatched_count + pending_count} articles · {total_amount}€`
- Subtitle: `"{matched_count} reconnus"` + `" · {unmatched_count + pending_count} à compléter"` if > 0. The user sees a single "to complete" category covering both unmatched (ready to scan the barcode) and pending (OCR in progress). The distinction appears in the row itself (🔴 vs ⏰).

#### New: `GET /api/v1/scan/label-group`

**Query params**:
- `store_id: UUID` — required
- `date: YYYY-MM-DD` — required (ISO date, UTC)

**Response**:
```json
{
  "items": [
    {
      "scan_id": "uuid",
      "product_name": "Yaourt Danone nature",
      "product_ean": "3033490004057",
      "price_cents": 115,
      "match_method": "barcode_ean",
      "scanned_at": "2026-04-24T09:30:00Z"
    }
  ]
}
```

**Filtering**: `status='accepted'` only (unmatched labels must NEVER appear).
**Ordering**: `scanned_at ASC` (chronological scan order).

#### Reused as-is: `POST /api/v1/scan/barcode`

No modification. Frontend uses existing endpoint to link a scanned EAN to an unmatched scan_item.

---

### Mapping `match_method` × `consensus_state` → button color

> **Updated 2026-05-01 (Bloc 9, pipeline_v3 rollout)** — v3 introduces two
> new statuses (`matched`, `unresolved`) and renames the `match_method`.
> The unified mapping now lives in `ratis_client/utils/scan-status.ts`
> (`mapStatusToUx` + `formatRejectedReason`). This table remains the product
> source of truth for UX decisions; the code respects it 1:1.
>
> **Updated 2026-05-01 PM — "green = consensus only"**: the large green dot
> is granted only to **explicit authority acts** (user barcode,
> manual_admin) OR automatic matches **validated by crowdsourced consensus**
> (`consensus_state='verified'`). Every other automatic match stays
> orange until the community confirms it. This rule overrides the original
> v3 table below (which did not account for consensus).

**Simple rule**:

- `barcode` / `barcode_ean` / `manual_admin` / `manual` → **always green**
  (explicit user/admin act, bypasses consensus).
- Any other `match_method` (fuzzy_pending, observed_name, knowledge,
  fuzzy_strict, fuzzy, fuzzy_confirmed, null, unknown) → **green ONLY
  if `consensus_state='verified'`**, otherwise **orange** (label
  `scan.status.matched_pending_consensus` = "Pending validation").

**v3 (canonical)**:

| `scan.status` | `scan.match_method` | `consensus_state` | Button color | Display name | Subtitle |
|---|---|---|---|---|---|
| `matched` | `barcode` | * (any) | 🟢 green | `product.name` | `OCR: {scanned_name}` if different, otherwise empty |
| `matched` | `manual_admin` | * (any) | 🟢 green (dimmed) | `product.name` | (empty — accessibility = "Manually validated") |
| `matched` | `knowledge` | `verified` | 🟢 green | `product.name` | (empty) |
| `matched` | `knowledge` | other / null | 🟠 orange | `product.name` | (empty — label "Pending validation") |
| `matched` | `fuzzy_pending` / `observed_name` / `fuzzy_strict` | `verified` | 🟢 green | `product.name` | (empty) |
| `matched` | `fuzzy_pending` / `observed_name` / `fuzzy_strict` | `unverified` / `controverse` / `pending` / `unresolved` / null | 🟠 orange | `product.name` | (empty — label "Pending validation") |
| `unresolved` | `null` | — | 🟠 orange | `scanned_name` or "Article à identifier" | `formatRejectedReason(rejected_reason)` translated |
| `rejected` | * | — | 🔴 red | `scanned_name` or placeholder | `formatRejectedReason(rejected_reason)` translated (defensive render — backend normally filters these) |
| `pending` | — | — | ⏰ grey (non-tappable) | `Traitement en cours…` (italic) | — |

**v2 (backward-compat — rows in DB pre-rollout)**:

| v2 status | Mapped to v3 UX (with consensus rule) |
|---|---|
| `accepted` + `barcode_ean` | `matched` + `barcode` (authority — always green) |
| `accepted` + `manual` | `matched` + `manual_admin` (authority — always green dimmer) |
| `accepted` + `observed_name` | `matched` + `knowledge` (consensus-gated) |
| `accepted` + `fuzzy` / `fuzzy_confirmed` | `matched` + `fuzzy_strict` (consensus-gated) — **rule change: was systematically green, now orange if not verified** |
| `unmatched` | `unresolved` (orange) — not red as before |

> **Why this rule?** The large green dot is read by the user
> as "ratis recognized the product with certainty". For an automatic match
> (fuzzy/observed) without crowdsourced validation, the error risk remains
> non-negligible — showing green would be misleading. A barcode scan is
> a user authority act (unique number, near-zero ambiguity), and
> `manual_admin` is an explicit human validation: these two cases
> keep direct green. Everything else goes through the community before
> being labeled "Recognized".

`rejected_reason` is translated by `formatRejectedReason()` (i18n keys
`scan.rejected_reason.*`). The pattern `fuzzy_below_threshold_<score>` or
`fuzzy_below_auto_accept_<score>` extracts the score (2 decimal places) which appears
in the label: "Match too weak (0.65)".

Tap 🟢/🟠/🔴 → opens `BarcodeScannerModal` → user scans EAN → `POST /scan/barcode` → refetch receipt items → color updates.

---

### Flows

**Flow A · Expand receipt accordion**
1. User taps receipt header
2. `expanded=true` → `useReceiptItems(receipt_id)` query enabled
3. During fetch: inline spinner in the body
4. Success: render colored items via `scan-history-item-row`

**Flow B · Link product by barcode**
1. User taps 🔴/🟠 button on an item
2. `BarcodeScannerModal` opens (camera + viewfinder)
3. User scans a barcode (EAN 8 or 13)
4. `onBarcode(ean)` called → `POST /scan/barcode {ean, scan_id}`
5. Success: invalidate `['receipt-items', receipt_id]` → refetch → row switches to 🟢
6. Error 409 `scan_already_resolved`: toast "Already resolved" + modal closes
7. Error 409 `product_mismatch`: toast "Barcode does not match the receipt" + modal stays open for rescan
8. Error 404 `product_not_found`: toast "Unknown product — we'll add it soon" + modal closes

**Flow C · Rescan receipt**
1. User taps "Rescanner" button on receipt header
2. `router.push('/(tabs)/scan')` — navigate to scan tab in receipt mode
3. User takes new photo → normal upload → backend detects same `receipt_barcode` → marks old scan `rejected='superseded_rescan'` automatically → creates new one

**Flow D · Expand label group**
1. User taps group header
2. `expanded=true` → `useLabelGroupItems(store_id, date)`
3. Success: render 🟢 items only (unmatched ones are not returned)

**Flow E · Infinite scroll**
1. User scrolls to the bottom
2. If `hasNextPage && !isFetching` → `fetchNextPage()` via cursor
3. Subtle spinner at the bottom of the list

**Flow F · Pull to refresh**
1. User swipes down
2. `refetch()` the first page → reset list

---

### i18n keys

Adding section `scan.history.*` in `ratis_client/locales/fr.json`:
```json
"scan": {
  "history": {
    "title": "Historique",
    "back": "Retour",
    "empty_title": "Aucun scan pour l'instant",
    "empty_hint": "Scanne ton premier ticket ou une étiquette pour démarrer.",
    "receipt": {
      "articles_count": "{{count}} articles",
      "articles_summary": "{{recognized}} reconnus · {{pending}} à compléter",
      "rescan_button": "Rescanner",
      "pending_ocr": "OCR en cours…"
    },
    "label_group": {
      "products_count": "{{count}} produits pris en compte"
    },
    "item": {
      "article_to_identify": "Article à identifier",
      "subtitle_ocr_brut": "OCR: {{text}}",
      "subtitle_match_uncertain": "Match incertain — vérifier",
      "subtitle_name_not_recognized": "Nom non reconnu dans la base",
      "subtitle_unmatched": "Non reconnu — scanne le code-barre",
      "subtitle_no_scan_name": "On sait qu'il y avait un produit ici",
      "subtitle_pending": "Traitement en cours…"
    },
    "relative_time": {
      "seconds": "à l'instant",
      "minutes": "il y a {{n}} min",
      "hours": "il y a {{n}}h",
      "days": "il y a {{n}}j",
      "weeks": "il y a {{n}} sem."
    },
    "barcode_modal": {
      "title": "Scanne le code-barre",
      "hint": "Positionne le code-barres du produit dans le cadre",
      "success": "✅ Produit lié",
      "error_mismatch": "⚠️ Ce code-barre ne correspond pas au ticket",
      "error_not_found": "❓ Produit inconnu — nous l'ajouterons bientôt",
      "error_generic": "❌ Erreur, réessaie"
    }
  }
}
```

---

### Tests to cover

**Frontend (`__tests__/app/scan-history.test.tsx` + components)**:
1. Renders empty state when no entries
2. Renders receipt accordion collapsed by default
3. Tap receipt header → fetches items → renders item rows with colored buttons
4. Tap label group header → fetches accepted items → renders rows
5. Item row: green button when match_method='barcode_ean'
6. Item row: orange button when match_method='fuzzy'
7. Item row: red button when status='unmatched'
8. Item row: clock icon (non-interactive) when status='pending'
9. Pending item: subtitle "Traitement en cours…"
10. Unmatched item with null scanned_name: display "Article à identifier" italic
11. Rescan button on receipt accordion: navigates to `/(tabs)/scan`
12. Barcode button tap: opens BarcodeScannerModal
13. BarcodeScannerModal onBarcode success: invalidates receipt-items query
14. BarcodeScannerModal error 409 mismatch: stays open, shows error toast
15. Pull to refresh: refetch called
16. Infinite scroll: fetchNextPage called when reaching end
17. Rejected scans never appear in any list (negative test)

**Backend (`webservices/ratis_product_analyser/tests/test_scan_history_service.py`)**:
1. `/scan/history`: receipts appear with matched/unmatched counts
2. `/scan/history`: labels grouped by `(store_id, date)`, count accepted only
3. `/scan/history`: groups with 0 accepted are hidden
4. `/scan/history`: rejected scans excluded
5. `/scan/history`: sorted by most recent activity DESC
6. `/scan/history`: stable cursor pagination
7. `/scan/receipt/{id}`: returns items with match_method
8. `/scan/receipt/{id}`: items sorted by order of appearance
9. `/scan/receipt/{id}`: rejected items excluded
10. `/scan/label-group`: accepted only
11. `/scan/label-group`: 404 if no scan for (store, date)
12. Barcode link re-run: receipt items change from unmatched → accepted

---

## V0.1 · Per-image GPS fix

**Motivation**:
Currently `/scan/label/batch` receives ONE global `(lat, lng)` pair and assigns the same coordinates to each `scans` row in the batch. A malicious user who scans 25 labels at Monoprix then 25 at Leclerc and sends everything in 1 batch → everything is attributed to the last GPS position → identical store_id for everything → incorrect grouping in history + potential fraud (Leclerc prices attributed to Monoprix, etc.).

**V0 scope without fix**: acceptable for family alpha (honest use case = 1 store per batch). Urgent fix before public launch.

### Frontend
- **Capture GPS at each photo** (not at send time)
- Local per-photo queue:
  ```ts
  interface PendingLabel {
    photoUri: string;
    lat: number;
    lng: number;
    captured_at: string; // ISO
  }
  ```
- At `PressCamera` time: `Location.getCurrentPositionAsync()` → adds the photo with its coords to the queue
- At `Send`: POST `/scan/label/batch` with enriched body

### Backend

**Before** (`BatchLabelRequest`):
```python
class BatchLabelRequest(BaseModel):
    images: list[bytes]  # via multipart
    lat: float
    lng: float
```

**After** (backward-compatible):
```python
class LabelImageMeta(BaseModel):
    lat: float
    lng: float
    captured_at: datetime

class BatchLabelRequest(BaseModel):
    images: list[bytes]                  # via multipart, stable order
    images_meta: list[LabelImageMeta] | None = None  # NEW, if None → legacy fallback
    lat: float | None = None             # legacy, optional if images_meta provided
    lng: float | None = None
```

**Service** `label_service._upload_and_create_scan`:
```python
for i, image in enumerate(images):
    meta = images_meta[i] if images_meta else LabelImageMeta(lat=lat, lng=lng, captured_at=datetime.utcnow())
    scan = Scan(
        ...
        user_lat=meta.lat,
        user_lng=meta.lng,
        scanned_at=meta.captured_at,
    )
```

Validation:
- If `images_meta` provided: `len(images_meta) == len(images)` otherwise 422
- If neither `images_meta` nor `(lat, lng)`: 422

### Migration / backward compatibility

- No DB migration (columns `user_lat`/`user_lng` already per-scan)
- Old clients (before V0.1): continue to work via global `(lat, lng)`
- Client V0.1+: sends `images_meta`
- After ~1 month of deployment: the legacy fallback can be removed (breaking change to announce)

### V0.1 Tests
1. Backend: batch with distinct `images_meta` → scans created with distinct coords
2. Backend: batch with mixed store A/B coordinates → distinct store_id resolved per scan
3. Backend: legacy global `(lat, lng)` still works
4. Backend: `len(images) != len(images_meta)` → 422
5. Frontend: each photo in the queue stores its own coords
6. Frontend: the send payload correctly includes the `images_meta` array

---

## Rules

- **TDD** — tests before code for each component + each backend modification (R01)
- **No raw SQL outside repositories** (R03) — group-by queries go in `scan_repository.py`
- **Layered arch**: routes → services → repositories
- **`db.commit()`** — no mutation in the new endpoints (read-only) except `POST /scan/barcode` which already has it
- **i18n**: all strings via `t('key')` (R frontend)
- **No PII** in logs (R17 RGPD) — `user_lat`/`user_lng` on scans is OK but never logged
- **Rejected filtering**: must be applied at the SQL level (performance) not on the frontend

---

## Out of scope

**Not V0, not V0.1 — to handle later**:
- Explicit filters by type / status / date in the UI
- Full-text search in history
- History export (CSV, PDF)
- Manual deletion of a scan by the user
- Statistics display (total CAB earned per month, cumulative savings, etc.)
- Receipt sharing (social)
- Reopening a receipt to correct the store if incorrectly resolved
