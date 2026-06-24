---
type: sub-arch
service: ratis_client
parent: ARCH_CLIENT
related: [ARCH_PRODUCT_ANALYSER, ARCH_barcode, ARCH_consensus]
status: in-progress
tags: [product, ean, prices, favorites, client]
updated: 2026-04-24
---

# ratis_client — ARCH Product Screen

> Product screen: EAN product sheet, local prices via consensus, favorites. Wired to the real API via `useProductByEan`. Static V1 catalogue fallback for incomplete product sheets.
> @tags: product ean prices favorites client product-analyser useProductByEan fiche-produit catalogue-statique
> @status: EN-COURS
> @subs: auto

> Parent: [[ARCH_CLIENT]] · Relations: [[ARCH_PRODUCT_ANALYSER]], [[ARCH_barcode]], [[ARCH_consensus]]

> Status: in progress
> Branch: `feature/produit-real-data`
> Updated 2026-04-21 — wired to the real API via `useProductByEan`.

---

## Implementation Checklist

**Base checklist:**
- [ ] Local types defined (`types/product.ts`)
- [ ] Static V1 catalogue + helper `getRandomIncomplete()` (`utils/product-catalogue.ts`)
- [ ] `EditableField` — TDD before code
- [ ] `product-hero.tsx` (ex-`ProductHeader`) — TDD before code
- [x] `product-consensus-card.tsx` — TDD before code
- [x] `product-price-row.tsx` — TDD before code
- [ ] `product-tabs.tsx` — TDD before code
- [ ] `ProductPricesTab` — TDD before code (empty state + list)
- [ ] `ProductInfoTab` — TDD before code
- [x] `produit.tsx` — two states (idle / detail) via param `?ean=`
- [ ] Tests written (TDD — before code)
- [ ] `ruff check --fix` n/a — `eslint` / TypeScript clean
- [ ] CI pipeline green

**Custom checklist:**
- [ ] "Price by store" tab active by default (royal violet tabs)
- [ ] Null fields → dashed orange background + "Add ✏️" text
- [ ] Inline editing: tap → in-place `TextInput`, `onBlur`/`onSubmitEditing` → save
- [ ] Random incomplete product: selection among products with ≥ 1 `null` field
- [ ] Empty price state: message + CTA "Scan this product" → navigate to Scan tab
- [ ] Deterministic store logo color via `getStoreAccent(storeName)`
- [ ] No favorites button ❤ (out of scope V1 — no backend endpoint)
- [ ] All strings in a `STRINGS` object per file (i18n preparation)

> ⚠️ One item at a time. Do not move to the next without completing the current one.

---

## Index

- [Context](#context) [L.48 - L.62]
- [Local types](#local-types) [L.64 - L.95]
- [Static V1 catalogue](#static-v1-catalogue) [L.97 - L.120]
- [Components](#components) [L.122 - L.210]
- [Screen produit.tsx](#screen-produittsx) [L.212 - L.265]
- [Navigation](#navigation) [L.267 - L.280]
- [Rules](#rules) [L.282 - L.292]
- [Out of scope](#out-of-scope) [L.294 - L.302]

---

## Context

Read before starting:
- `CLAUDE.md`
- `KNOWN_PROBLEMS_INDEX.md`
- `DECISIONS_ACTED.md`
- `ratis_client/ARCH_scan.md` — same stack, same patterns
- `ratis_client/ARCH_design_system.md` if it exists

Required dependencies:
- `@/utils/shopping-totals` — `formatCents()` already available
- `@/constants/theme` — `Design.colors` (enriched theme v2 palette: coral, royalViolet, gold, red, orange)
- `@/components/ui/screen-background` — shared background (Ratis image + fog + glows)
- `@/components/ui/app-header` — shared sticky header (CAB + season + store/missions icons)
- `@/components/ui/page-title-band` — grey band below AppHeader (title + actions)
- `@/components/ui/cards/screen-card` — glass-morphism wrapper with accent variants
- `@/utils/store-accent` — `getStoreAccent(storeName)` for deterministic color per store
- `expo-router` — `useLocalSearchParams`, `router.push`

Reference spec: `docs/superpowers/specs/2026-04-20-screens-theme-v2-design.md` (replaces old spec `docs/superpowers/specs/_archive/2026-04/2026-04-18-product-screen-design.md`)

Backend endpoint to create (documented in PROD_CHECKLIST):
- `GET /api/v1/products/random-incomplete` — not available in V1, replaced by static `getRandomIncomplete()`

---

## Local types

```ts
// ratis_client/types/product.ts

export interface ProductDetail {
  ean:             string
  name:            string | null
  brand:           string | null
  photoUrl:        string | null
  category:        string | null
  unit:            string | null   // ex: "400g", "1L", "1 unité"
}

export interface ProductPrice {
  storeName:   string
  priceCents:  number
  updatedAt:   number  // timestamp ms
}

export interface IncompleteProduct extends ProductDetail {
  missingFields: (keyof ProductDetail)[]
}
```

---

## Static V1 catalogue

```ts
// ratis_client/utils/product-catalogue.ts

export const PRODUCT_CATALOGUE: ProductDetail[]
// 9 products minimum, of which ≥ 3 with null fields

export const PRODUCT_PRICES: Record<string, ProductPrice[]>
// Prices for 3–4 products from the catalogue. Others have an empty array → empty state.

export function getRandomIncomplete(): IncompleteProduct
// Filters PRODUCT_CATALOGUE for products with ≥ 1 null field.
// Pseudo-random selection (Math.random).
// Computes missingFields = Object.keys filtered on null value.
```

---

## Components

### `EditableField`

```tsx
// ratis_client/components/product/editable-field.tsx

interface EditableFieldProps {
  label:   string          // field label (future i18n key)
  value:   string | null   // null = missing field
  onSave:  (v: string) => void
  testID?: string
}
```

Behaviour:
- `value !== null` → background `#2a3f4e`, white text + "✏️"
- `value === null` → background `rgba(255,183,0,0.07)`, dashed border `rgba(255,183,0,0.35)`, "Add ✏️" text in `#FFB800`
- Tap → `editing=true` → `TextInput` `autoFocus`, same background + teal border `#00D9B5`
- `onBlur` or `onSubmitEditing` → `onSave(newValue.trim())` → `editing=false`
- If `newValue.trim() === ''` → do not call `onSave`, restore the original value

---

### `ProductHero` (ex-`ProductHeader`)

```tsx
// ratis_client/components/produit/product-hero.tsx

interface ProductHeroProps {
  product:  ProductDetail
}
```

Layout:
```
[ 📦 photo (72×72) ]  [ BRAND (uppercase muted) ]
                      [ Product name (bold) ]
                      [ EAN (muted, small) ]
```

Clean render, no kraft/poster background. The consensus price block + tabs are extracted into dedicated components below.

---

### `ProductConsensusCard` (new)

```tsx
// ratis_client/components/produit/product-consensus-card.tsx

interface ProductConsensusCardProps {
  priceCents:   number | null
  scanCount:    number
  trustScore:   number   // 0–100
}
```

Card with royal violet accent (`<ScreenCard accent="violet">`):
```
┌ CONSENSUS PRICE ──────────────┐
│ 1,25€                         │
│ 142 scans · reliability 97%   │
└───────────────────────────────┘
```

- Central price in large font (royal violet if present, muted + `"—"` if null)
- Meta subtitle: `N scans · reliability X%` in muted

---

### `ProductTabs` (refactor)

```tsx
// ratis_client/components/produit/product-tabs.tsx

interface ProductTabsProps {
  activeTab: 'prix' | 'infos'
  onTabChange: (tab: 'prix' | 'infos') => void
}
```

Royal violet pill tabs (`royalViolet` — `#7C3AED`):
```
[ Price by store* ] [ Info ]
```

- `*` = active, light royal violet background + violet border + white text
- Inactive: transparent + muted text
- Replaces old teal bottom-border pattern

---

### `ProductPricesTab` ("Price by store" tab content)

```tsx
// ratis_client/components/produit/product-prices-tab.tsx

interface ProductPricesTabProps {
  prices:       ProductPrice[]
  onScanPress:  () => void
}
```

- `prices.length > 0` → list of `ProductPriceRow` sorted by ascending `priceCents`, inside a `<ScreenCard noPadding>`. Best price in gold, others in white.
- `prices.length === 0` → centred empty state: 🔍 emoji + title + subtitle + royal violet button "📷 Scan this product" → `onScanPress()`

### `ProductPriceRow` (new)

```tsx
// ratis_client/components/produit/product-price-row.tsx

interface ProductPriceRowProps {
  storeName:   string
  distanceKm?: number
  priceCents:  number
  isBestPrice: boolean
  updatedAt:   number
}
```

Inline layout:
```
[ L ]  Leclerc Parmentier · 0,4km   1,19€
[ M ]  Monoprix Rép       · 0,8km   1,29€
```

- 28×28 logo badge: background color via `getStoreAccent(storeName)` (coral / gold / royalViolet / red / orange — deterministic hash), initial letter in white
- Price on the right: gold (`#FFB800`) if `isBestPrice`, otherwise white
- Age `formatRelativeDate(updatedAt)` in muted below the store name

---

### `ProductInfoTab`

```tsx
// ratis_client/components/product/product-info-tab.tsx

interface ProductInfoTabProps {
  product:   ProductDetail
  onUpdate:  (field: keyof ProductDetail, value: string) => void
}
```

- Displays one `EditableField` per editable field: `name`, `brand`, `category`, `unit`
- `ean` displayed as read-only (no editing)
- `photoUrl`: placeholder in V1 (not editable — "📷 V2")
- Display order: name → brand → category → quantity → photo

---

### ~~`IncompleteProductCard`~~ (removed in theme v2)

Replaced by a simple `<ScreenCard accent="orange">` embedding a list of `EditableField` on missing fields, without a branded header or "Earn CABecoins" CTA. The block retains its purpose (completing a random product) but the rendering switches to a clean glass card. The `getRandomIncomplete()` helper and the `IncompleteProduct` type remain valid — only the rendering changes.

---

## Screen `produit.tsx`

```tsx
// ratis_client/app/(tabs)/produit.tsx

const params = useLocalSearchParams<{ ean?: string }>()
const ean = params.ean ?? DEFAULT_EAN  // fallback dev EAN
```

### Data flow (V1 wired to API)

```
route params (ean)
        │
        ├─> expo-location.requestForegroundPermissionsAsync()
        │       └─> getCurrentPositionAsync() → { lat, lng }
        │
        └─> useProductByEan(ean, { lat, lng })
                └─> GET /api/v1/product/{ean}?user_lat=…&user_lng=…
                        └─> { product, local_price, nearby_prices[] }
```

- `MOCK_DETAIL` removed — all data comes from the API
- Geolocation best-effort: if denied (`locationStatus === 'denied'`), the screen remains usable but the price list is empty → banner `"Enable location to see prices"`
- `bestPriceCents` = `Math.round(prices[0].price * 100)` after ascending sort
- `storesCount` = `prices.length` feeds `<ProductConsensusCard storesCount={…} />`

### UI States

```
[ ScreenBackground ]
[ AppHeader ]
[ PageTitleBand title="Product detail" titleSize="small"
    leftIcon={←} rightIcons={[♥/♡, ↗]} ]
[ SafeAreaView ]
  ├─ isLoading && !data → <ActivityIndicator testID="produit-loading" />
  ├─ isError           → "Product not found" + Retry button (refetch)
  └─ product present   → ScrollView
        [ Hero: photo_url | 📦 placeholder + brand + name + ean ]
        [ ProductConsensusCard priceCents={bestPriceCents} storesCount={N}
            locationDenied={locationStatus === 'denied'} ]
        [ Tabs Price / Info (royal violet) ]
        ├─ price: sorted ScreenCard list (isBest on idx 0) or empty banner
        └─ info: placeholder "Product info (V2)"
```

- The favorite (`useIsFavorite`/`useToggleFavorite` from PR #56) uses the current ean (no more `MOCK_DETAIL.ean` constant)

### V2 (out of current scope)

- Search / browse mode without geolocation → fallback on stored user address
- Display favorites in home/profile
- Scanned products history
- `ProductInfoTab` — editing missing fields (`EditableField`) wired to a PATCH
- Name search + static `PRODUCT_CATALOGUE` removed (initial V1)

---

## Navigation

### To the product sheet (from other screens)

```ts
// From scan.tsx, liste.tsx, index.tsx
import { router } from 'expo-router'
router.push(`/(tabs)/produit?ean=${product.ean}`)
```

In V1 this wiring is not implemented — the tab is standalone.

### From the sheet → Scan (CTA empty Price state)

```ts
router.push('/(tabs)/scan')
```

---

## Rules

- All visible strings in a `STRINGS` object at the top of the file — never literal strings in JSX
- `ean` always displayed as read-only — no editing possible
- `photoUrl`: not editable in V1 — placeholder displayed, "V2" badge
- Amounts always in cents in types, `formatCents()` for display
- `onSave('')` ignored — an empty field reverts to `null` (unmodified)
- Price sort: ascending by `priceCents` — best price = first in the list
- `getRandomIncomplete()` recomputed on each idle screen mount — no persistence

---

## Out of scope

- Real API calls (catalogue, prices, PATCH product) — V2
- Inbound navigation from liste.tsx / scan.tsx / index.tsx — V2
- Editable product photo — V2
- Field edit history — out of V1
- National OFF price as fallback — out of V1
- Enum fields (category with dropdown, unit with picker) — after DB schema stabilisation
- Favorites button ❤ — removed V1 (no backend endpoint, to reopen when `/products/{ean}/favorite` exists)
