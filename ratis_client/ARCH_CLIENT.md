---
# Identité
type: client-global
service: ratis_client
status: in-progress

# Navigation (Obsidian + RAG)
parent: ARCH_RATIS
sub_archs: [ARCH_auth, ARCH_design_system, ARCH_expo_strategy, ARCH_feed_jack, ARCH_liste, ARCH_product, ARCH_profil, ARCH_scan, ARCH_scan_history]
related: [ARCH_AUTH, ARCH_REWARDS, ARCH_PRODUCT_ANALYSER, ARCH_LIST_OPTIMISER, ARCH_CORE]

# Technique
tech: [Expo SDK54, React Native, expo-router, TypeScript, i18next, React Query, expo-auth-session, EAS, Sentry]
tables: []
env_vars: [EXPO_PUBLIC_API_URL, EXPO_PUBLIC_REWARDS_API_URL, EXPO_PUBLIC_PRODUCT_API_URL, EXPO_PUBLIC_LIST_API_URL]

# Business
tags: [frontend, mobile, expo, react-native, ui, ios, android]
business_domain: infra
rgpd_concern: true

# Freshness (MANDATORY — R34)
updated: 2026-04-24
---

# ratis_client — ARCH Expo/React Native mobile app

> Ratis mobile application (Expo SDK54 + React Native + expo-router + TypeScript): 4 tabs (dashboard, list, scan, product, profile), 30+ React Query hooks, OAuth Google+Apple, OTA via EAS Update. Shared TS/JS code, limited native modules.
> @tags: frontend mobile expo react-native ui ios android expo-router eas eas-update typescript react-query i18next sentry
> @status: EN-COURS
> @subs: auto

> Parent: [[ARCH_RATIS]] · Sub-ARCHs: [[ARCH_auth]] · [[ARCH_design_system]] · [[ARCH_expo_strategy]] · [[ARCH_feed_jack]] · [[ARCH_liste]] · [[ARCH_product]] · [[ARCH_profil]] · [[ARCH_scan]] · [[ARCH_scan_history]] · Relations: [[ARCH_AUTH]] · [[ARCH_PRODUCT_ANALYSER]] · [[ARCH_LIST_OPTIMISER]] · [[ARCH_REWARDS]] · [[ARCH_CORE]]

## Index

- [One-sentence summary](#one-sentence-summary) · L.46
- [Responsibilities](#responsibilities) · L.50
- [Structure](#structure) · L.59
- [Tech stack](#tech-stack) · L.130
- [Backend dependencies (the 4 consumed webservices)](#backend-dependencies-the-4-consumed-webservices) · L.148
- [Sub-ARCHs](#sub-archs) · L.157
- [Key architecture decisions](#key-architecture-decisions) · L.169
- [Main flows](#main-flows) · L.219
- [GDPR constraints specific to ratis_client](#gdpr-constraints-specific-to-ratis_client) · L.258
- [Vectorised FAQ](#vectorised-faq) · L.268
- [Glossary](#glossary) · L.302

---

## One-sentence summary

ratis_client is the Ratis mobile application built on Expo SDK 54 / React Native 0.81 (iOS + Android), written in TypeScript with expo-router (file-based), which consumes the 5 FastAPI backend webservices via 4 dedicated HTTP clients (`services/api-client.ts`, `rewards-client.ts`, `product-client.ts`, `list-client.ts`) and stores JWTs in SecureStore to offer users receipt scanning, optimised shopping lists, product pages with real-time prices, gamification (CAB + battle pass + streak), and a profile/referral screen.

## Responsibilities

- ratis_client exposes **4 main tabs** to the user via expo-router: `index` (dashboard missions / BP / streak / CAB), `liste` (shopping list + route optimisation), `scan` (receipt / label camera with background queue), `produit` (EAN lookup + favourites + nearby prices), `profil` (stats + menu).
- ratis_client handles **OAuth authentication** (Google via expo-auth-session, Apple via expo-apple-authentication iOS-only) and the access/refresh JWT cycle with automatic refresh on 401.
- ratis_client hosts the **background scan upload queue** (`services/scan-queue.ts`) so a user can scan multiple receipts in a row even offline, with automatic retry on reconnection.
- ratis_client applies **internationalisation via i18next** (V0 French only, catalogue `locales/fr.json`), translating the `detail="snake_code"` error codes returned by the backends.
- ratis_client embeds the **Ratis design system** (colours, typography, components — see [[ARCH_design_system]]), the mascot character Jack (see [[ARCH_feed_jack]]), and the reward animations (ring, haptics).
- ratis_client provides a **dev-bypass** for login only in `__DEV__` mode in `app/(auth)/login.tsx` to ease local development (disabled in production builds).

## Structure

Directory: `ratis_client/`

```
ratis_client/
├── app/                         # expo-router screens (file-based routing)
│   ├── _layout.tsx              # root layout + AuthProvider + I18nextProvider + QueryClientProvider + Sentry
│   ├── modal.tsx                # generic modal
│   ├── my-info.tsx              # profile editing + change password
│   ├── referral.tsx             # referral code + copy + share + stats + history
│   ├── (auth)/
│   │   ├── _layout.tsx
│   │   └── login.tsx            # OAuth Google + Apple (iOS) + dev-bypass __DEV__
│   └── (tabs)/
│       ├── _layout.tsx          # bottom-tab navigator (5 tabs)
│       ├── index.tsx            # Dashboard: missions / BP / streak / CAB
│       ├── liste.tsx            # Shopping List + route optimisation (Products tab + Itinerary tab)
│       ├── scan.tsx             # Camera receipt / label + bg-queue
│       ├── produit.tsx          # EAN lookup + nearby prices + favourites
│       ├── profil.tsx           # Stats + menu (V0: my-info + referral active, rest greyed-out)
│       └── explore.tsx          # (legacy template — to be cleaned up)
├── components/                  # reusable components
│   ├── dashboard/, liste/, produit/, profil/, scan/, ui/
│   ├── AppCrashScreen.tsx       # Sentry fallback on fatal crash
│   ├── ErrorBanner.tsx
│   ├── GoogleButton.tsx         # branded Google OAuth button
│   ├── LegalFooter.tsx
│   ├── ratis-mascot.tsx         # Jack (see ARCH_feed_jack)
│   ├── ratis-tab-bar.tsx        # custom tab-bar
│   ├── themed-text.tsx, themed-view.tsx
│   ├── haptic-tab.tsx, hello-wave.tsx, parallax-scroll-view.tsx
│   └── external-link.tsx
├── contexts/
│   ├── AuthContext.tsx          # global auth state + JWT refresh + logout
│   └── authReducer.ts
├── hooks/                       # React Query / business hooks
│   ├── useAuth.ts, use-auth-me.ts, use-update-profile.ts, use-change-password.ts
│   ├── use-cab-balance.ts, use-missions.ts, use-battlepass.ts, use-streak.ts, use-account-stats.ts, use-claim-ring.ts
│   ├── use-product-by-ean.ts, use-favorites.ts, use-enrichissement.ts
│   ├── use-referral-code.ts, use-referral-history.ts
│   ├── use-shopping-lists.ts, use-shopping-list-detail.ts, use-list-items.ts, use-active-route.ts, use-optimize-route.ts
│   ├── use-scan-history.ts, use-scan-check.ts
│   └── use-color-scheme.ts, use-theme-color.ts
├── services/                    # API + infrastructure layer
│   ├── api-client.ts            # fetch wrapper → EXPO_PUBLIC_API_URL (ratis_auth)
│   ├── rewards-client.ts        # → EXPO_PUBLIC_REWARDS_API_URL (ratis_rewards)
│   ├── product-client.ts        # → EXPO_PUBLIC_PRODUCT_API_URL (ratis_product_analyser)
│   ├── list-client.ts           # → EXPO_PUBLIC_LIST_API_URL (ratis_list_optimiser)
│   ├── auth-service.ts, auth-events.ts
│   ├── scan-queue.ts, scan-events.ts   # background upload queue (expo-background-fetch + expo-task-manager)
│   ├── token-storage.ts         # SecureStore wrapper iOS / EncryptedSharedPreferences Android
│   ├── wait-for-online.ts       # reconnection helper (expo-network)
│   ├── logger.ts
│   └── sentry.ts                # init @sentry/react-native
├── locales/
│   └── fr.json                  # i18next catalogue (V0 fr only)
├── __tests__/                   # jest + @testing-library/react-native (460+ tests)
├── __mocks__/                   # MSW mocks for backends
├── assets/                      # images + fonts
├── constants/
├── lib/, utils/, types/
├── tests/
├── scripts/
├── app.json                     # Expo config (scheme, splash, icon, permissions, versions)
├── eas.json                     # EAS profiles: development / preview / production
├── package.json                 # dependencies (Expo SDK54, RN 0.81, React 19.1, React Query 5, i18next 26)
├── tsconfig.json, jest.config.js, jest.setup.js, eslint.config.js, metro.config.js
└── ARCH_*.md                    # sub-ARCHs (auth, design_system, expo_strategy, feed_jack, liste, product, profil, scan, scan_history)
```

## Tech stack

- **Expo SDK 54** (expo ~54.0.33) + **React Native 0.81** + **React 19.1** — managed workflow (no eject, see [[ARCH_expo_strategy]])
- **TypeScript** 5.9
- **expo-router** ~6.0 — file-based routing (each file in `app/` is a route, `(groups)` organise without URL segment)
- **@tanstack/react-query** 5.99 — cache + revalidation of backend calls
- **i18next** + **react-i18next** — translation (V0 `fr.json` only)
- **expo-auth-session** + **expo-apple-authentication** — OAuth Google + Apple
- **expo-secure-store** — JWT storage (iOS Keychain / Android EncryptedSharedPreferences)
- **expo-camera** — receipt / label capture
- **expo-background-fetch** + **expo-task-manager** — background scan upload queue
- **expo-location** + **expo-network** — geolocation (current store) + connectivity detection
- **@sentry/react-native** ~7.2 — crash + error observability
- **expo-haptics** — haptic feedback on CAB claim / validated scan
- **react-native-reanimated** ~4.1 + **react-native-worklets** — reward animations
- **react-native-svg** — icons + illustrations
- **EAS** (Expo Application Services) — iOS/Android builds, profiles `development` / `preview` / `production` in `eas.json`

## Backend dependencies (the 4 consumed webservices)

- [[ARCH_AUTH]] via `services/api-client.ts` → env `EXPO_PUBLIC_API_URL` — OAuth login, refresh, `/account/*`
- [[ARCH_REWARDS]] via `services/rewards-client.ts` → env `EXPO_PUBLIC_REWARDS_API_URL` — CAB balance, missions, battle pass, streak, gift cards, referral
- [[ARCH_PRODUCT_ANALYSER]] via `services/product-client.ts` → env `EXPO_PUBLIC_PRODUCT_API_URL` — scan upload, EAN product page, consensus prices
- [[ARCH_LIST_OPTIMISER]] via `services/list-client.ts` → env `EXPO_PUBLIC_LIST_API_URL` — shopping lists, itinerary optimisation

Note: ratis_client **never** calls [[ARCH_NOTIFIER]] directly — push notifications are handled server-side (ratis_notifier pushes to the Expo Push Service which delivers to the app).

## Sub-ARCHs

- [[ARCH_auth]] — OAuth Google/Apple authentication, `AuthContext`, refresh token + 401 handler, router guards.
- [[ARCH_design_system]] — Ratis design system: colours, typography, spacing, components, animations, foreground notifications.
- [[ARCH_expo_strategy]] — Expo V1 strategy and signals for migration to bare React Native.
- [[ARCH_feed_jack]] — Mascot character "Jack" (visual identity, states, daily streak).
- [[ARCH_liste]] — List screen: products tab + itinerary tab, API hooks, scan-to-check-off flow, build → optimize → shop.
- [[ARCH_product]] — Product screen: EAN lookup, nearby prices, favourites, static V1 catalogue.
- [[ARCH_profil]] — Profile screen: stats + menu (V0 active: my-info + referral; greyed out: Shop, Achievements, Notifications).
- [[ARCH_scan]] — Scan screen: always-on camera, background upload queue, scan history, unknown store.
- [[ARCH_scan_history]] — Scan History screen (V0 in progress).

## Key architecture decisions

### DA-01 — Expo managed workflow (no bare RN)

**Choice**: ratis_client stays in Expo managed workflow, built exclusively via EAS.
**Rejected alternative**: eject to bare React Native (direct access to Xcode / Android Studio projects).
**Reason**: Expo SDK 54 covers all native modules needed for V0/V1 (camera, secure-store, location, apple-auth, google-auth, background-fetch, task-manager, haptics). Ejecting would explode maintenance complexity (manual Xcode/Gradle, local native builds). Migration signals toward bare are monitored and documented in [[ARCH_expo_strategy]] — we will migrate only if a critical native module is missing or if Expo blocks a V2+ feature.

### DA-02 — expo-router (file-based routing)

**Choice**: ratis_client uses expo-router ~6.0 — each file in `app/` is a route, folders in parentheses such as `(auth)` and `(tabs)` are groups that do not count in the URL.
**Rejected alternative**: React Navigation 7 directly (more verbose, fewer conventions).
**Reason**: expo-router has been the Expo recommendation since SDK 50, aligned with Next.js routing that the whole team already knows. It automatically handles deep links via the `app.json` scheme, router guards via layouts, and integrates natively with push notifications. Adoption cost is low: one file = one route.

### DA-03 — React Query for all backend requests

**Choice**: all backend calls go through `@tanstack/react-query` (hooks `use-*.ts`), never raw `useEffect + fetch`.
**Rejected alternative**: SWR or manual fetch + useState.
**Reason**: React Query handles out-of-the-box key-based cache with invalidation (after a mutation, we invalidate `cab-balance` and `missions`), focus revalidation, automatic retry, and `loading/error/success` states. This lets ratis_client instantly display cached values (CAB balance, missions) while revalidating in the background — significantly better UX than a systematic loading spinner.

### DA-04 — i18next fr-only V0

**Choice**: ratis_client is fully translated via `t('key')` i18next, with a single `locales/fr.json` catalogue in V0.
**Rejected alternative**: hard-coded French strings in components.
**Reason**: internationalisation is planned for V1+ (UK, DE, ES — see [[ARCH_deployment]]). Introducing i18next from V0 avoids a massive refactor later, and allows backends to return `detail="snake_code"` errors translated uniformly on the client. The V0 cost is marginal (a single `fr.json` file to maintain), the V1 benefit is enormous.

### DA-05 — SecureStore for JWTs (not AsyncStorage)

**Choice**: `services/token-storage.ts` wraps `expo-secure-store` to store the access token and the refresh token. AsyncStorage is never used for sensitive data.
**Rejected alternative**: AsyncStorage + custom encryption.
**Reason**: SecureStore uses iOS Keychain and Android EncryptedSharedPreferences — OS-level encrypted storage, inaccessible from other apps. AsyncStorage is stored in plain text on the filesystem (GDPR violation for auth tokens that grant access to all user data). The `token-storage.ts` wrapper allows switching to native Keychain/Keystore without touching consumers if needed.

### DA-06 — 4 separate HTTP clients (one per backend service)

**Choice**: 4 distinct `services/*-client.ts` files (`api-client`, `rewards-client`, `product-client`, `list-client`), each pointing to its own `EXPO_PUBLIC_*_API_URL` env var.
**Rejected alternative**: a single client with a `service: 'auth' | 'rewards' | ...` parameter.
**Reason**: 4 separately typed clients give precise TypeScript typing (no painful discriminated union) and allow one URL per service (useful for pointing to a different staging environment per service if needed). The duplication cost is low because the JWT refresh wrapper is factored into `auth-service.ts`. Since the 4 services share the same JWT (DA-02 of [[ARCH_RATIS]]), the `Authorization` header is identical everywhere.

### DA-07 — Background scan queue (expo-background-fetch)

**Choice**: `services/scan-queue.ts` uses `expo-background-fetch` + `expo-task-manager` to upload scans in the background even when the user closes the app.
**Rejected alternative**: blocking synchronous upload inside the `scan.tsx` route.
**Reason**: users often scan several receipts in a row, sometimes offline (in a basement store). A synchronous upload would block the UI and lose offline scans. The background queue persists scans locally, retries automatically on reconnection (via `wait-for-online.ts`), and honours the fire-and-forget principle (red line of [[ARCH_RATIS]]).

### DA-08 — Jest + @testing-library/react-native (460+ tests)

**Choice**: ratis_client uses Jest 29 + @testing-library/react-native + MSW to mock backends. 460+ tests cover hooks, components, and flows.
**Rejected alternative**: Detox (E2E on simulated device).
**Reason**: unit + component tests are sufficient in V0/V1 to catch the vast majority of regressions. Detox (E2E) would be useful but is expensive in CI and maintenance. It will be introduced if unit tests miss critical post-release regressions.

## Main flows

### Flow 1 — Google OAuth Login

1. The user opens the app, `app/(auth)/login.tsx` is displayed (no JWT in SecureStore).
2. They tap "Continue with Google" → `expo-auth-session` opens the Google OAuth window and retrieves an `id_token`.
3. `services/auth-service.ts` calls `POST /api/v1/auth/login/google` of [[ARCH_AUTH]] with the `id_token`.
4. [[ARCH_AUTH]] returns `access_token` (60 min) + `refresh_token` (30d) + `user`.
5. `services/token-storage.ts` stores the tokens in SecureStore.
6. `contexts/AuthContext.tsx` updates the state, `expo-router` redirects to `app/(tabs)/index.tsx`.
7. See [[ARCH_auth]] for the full detail, the refresh token + 401 handler, and the `__DEV__` dev-bypass.

### Flow 2 — Receipt Scan

1. The user opens `app/(tabs)/scan.tsx` — `expo-camera` is always active.
2. They take a photo → the image goes into `services/scan-queue.ts` (queue persisted locally).
3. If online: immediate upload via `product-client.ts` → `POST /api/v1/scan/receipt` of [[ARCH_PRODUCT_ANALYSER]].
4. If offline: the background task `expo-background-fetch` retries on the next network wake-up (`wait-for-online.ts`).
5. The backend processes asynchronously (OCR + matching + consensus).
6. An Expo push notification alerts the user when the scan is validated, [[ARCH_REWARDS]] credits the CABs.
7. See [[ARCH_scan]] and [[ARCH_scan_history]] for the detail.

### Flow 3 — Shopping Route Optimisation

1. The user edits their list in `app/(tabs)/liste.tsx` Products tab (hook `use-shopping-list-detail`).
2. They switch to the Itinerary tab → `use-optimize-route` calls `POST /api/v1/lists/{id}/optimize` of [[ARCH_LIST_OPTIMISER]].
3. [[ARCH_LIST_OPTIMISER]] sends to a Celery worker that uses OSRM to compute the multi-store itinerary.
4. The route is stored for 24h (`optimized_routes`, TTL) without a home point (PII).
5. The client receives the list grouped by store + optimal visit order.
6. See [[ARCH_liste]] for the UI flow detail.

### Flow 4 — Product page lookup by EAN

1. The user scans an EAN barcode in `app/(tabs)/produit.tsx`.
2. `use-product-by-ean` calls `GET /api/v1/product/{ean}` of [[ARCH_PRODUCT_ANALYSER]].
3. The backend returns the product page + list of nearby store prices (via user geolocation passed as a parameter, not stored server-side).
4. The user can add to favourites (`use-favorites`) or to the current list.
5. See [[ARCH_product]] for the detail.

## GDPR constraints specific to ratis_client

- **JWTs in SecureStore only** — never in AsyncStorage (see DA-05). Logout = immediate token deletion.
- **User geolocation** (`expo-location`) — transmitted to the backend for store resolution and price enrichment, **never logged** on the client side (no `console.log(coords)`, no Sentry breadcrumb with lat/lng).
- **No PII in AsyncStorage** — only the volatile React Query cache may hold personal data in memory, cleared on logout.
- **Route steps without home-point** — [[ARCH_LIST_OPTIMISER]] never returns the home point in `optimized_routes.steps`; the client also never stores it anywhere.
- **`DELETE /account`** — called via `api-client.ts`, triggers backend in-place anonymisation, then logout + SecureStore reset on the client.
- **Sentry breadcrumbs filtered** — `services/sentry.ts` scrubs `Authorization` headers and payloads containing `password`, `id_token`, `refresh_token`.
- **Dev-bypass login only in `__DEV__`** — the bypass button in `app/(auth)/login.tsx` is compile-conditional, absent from the production build (EAS production profile).

## Vectorised FAQ

### Why Expo and not React Native CLI?

ratis_client uses Expo managed workflow (SDK 54) because Ratis needs an aggressive time-to-market and all the required V0/V1 native modules are available in Expo (camera, secure-store, location, apple-auth via expo-apple-authentication, google-auth via expo-auth-session, background-fetch, task-manager, haptics, network, linear-gradient). Ejecting to bare React Native would explode build complexity (manual Xcode / Gradle, no EAS cloud builds) with no V1 benefit. The thresholds for migrating to bare are documented and monitored — see [[ARCH_expo_strategy]].

### Why expo-router (file-based) and not React Navigation directly?

ratis_client uses expo-router ~6.0, which has been the Expo recommendation since SDK 50 and is internally backed by React Navigation 7. File-based routing (each file in `app/` is a route, groups `(auth)` / `(tabs)`) is familiar to the whole team used to Next.js, reduces boilerplate, natively handles deep links via the `app.json` scheme, and facilitates router guards via layouts. Going directly through React Navigation would require manually declaring all navigators, screens, and params — more verbose, more prone to typos.

### Why React Query and not Redux + thunks?

ratis_client spends 90% of its time displaying backend data (CAB balance, missions, lists, products, scan history) — a global Redux store + thunks would be overkill and costly in boilerplate. React Query handles out-of-the-box key-based cache, focus revalidation, retry, loading/error/success states, and mutation with invalidation. Purely local state (auth, UI) fits in `contexts/AuthContext.tsx` + a useState per screen. Redux will be introduced only if a complex global state appears (never needed in V0/V1).

### How does ratis_client store authentication tokens?

ratis_client stores the access token (60 min) and the refresh token (30d) via `expo-secure-store` in `services/token-storage.ts`. On iOS this uses Keychain, on Android EncryptedSharedPreferences — OS-encrypted storage, inaccessible to other apps. AsyncStorage is **never** used for tokens (plain-text storage on the FS, GDPR violation). On logout (or on `DELETE /account`), the tokens are immediately deleted from SecureStore. Automatic refresh on 401 is handled by `services/auth-service.ts` with a lock to prevent concurrent refreshes.

### How does ratis_client work offline?

Scanning is offline-resilient: `services/scan-queue.ts` persists captured images locally (app directory), and `expo-background-fetch` + `expo-task-manager` upload scans on the next network wake-up detected by `expo-network` (helper `wait-for-online.ts`). Other screens (dashboard, list, product, profile) work in "last cached value" mode thanks to React Query: the CAB balance, missions, and shopping list are displayed from cache and revalidated when the connection returns. Mutating actions outside of scanning (e.g. adding a product to the list) do require being online to be confirmed server-side.

### Why 4 separate HTTP clients?

ratis_client consumes 4 distinct backend webservices (ratis_auth, ratis_rewards, ratis_product_analyser, ratis_list_optimiser), each on its own URL via the `EXPO_PUBLIC_*_API_URL` env var. Having 4 dedicated `services/*-client.ts` files (rather than a generic client with a `service` parameter) gives precise TypeScript typing per service, allows targeting independent staging environments if needed, and aligns client structure with backend structure. The `Authorization: Bearer <jwt>` header is factored into `services/auth-service.ts` and the 4 clients share the same JWT (see DA-02 of [[ARCH_RATIS]]).

### How to test ratis_client locally?

Prerequisites: `cd ratis_client && npm install`. To launch the app: `npx expo start` (opens the dev menu, scan QR with Expo Go or launch on simulator). For unit tests: `npm test` (Jest + @testing-library/react-native + MSW to mock backends). In V0/V1 the suite contains 460+ tests. Mocked backends are in `__mocks__/` and use MSW to intercept requests. For an EAS build: `eas build --profile preview --platform android` (test APK) or `eas build --profile production` (store build).

### Why i18next from V0 when Ratis is fr-only?

ratis_client uses i18next + `locales/fr.json` in V0 for two reasons: (1) internationalisation is planned for V1+ (UK, DE, ES — see the V1 strategy in [[ARCH_deployment]]), and introducing i18next from V0 avoids a massive refactor later that would touch all components. (2) Backends return snake_case error codes (`detail="email_already_taken"`) and it is the client's job to translate them — i18next is the natural mechanism for that. The V0 cost is marginal (a single fr.json file to maintain) and the ergonomics are identical to hard-coded strings for the developer who writes `t('errors.email_already_taken')`.

## Glossary

- **EAS**: Expo Application Services — Expo cloud build service (iOS + Android, OTA updates, store submission)
- **OTA**: Over-The-Air update — JavaScript bundle update without going through a new store build, via EAS Update (not enabled in V0)
- **SecureStore**: Expo module that stores encrypted strings via iOS Keychain / Android EncryptedSharedPreferences
- **MSW**: Mock Service Worker — intercepts HTTP requests in tests to simulate backends
- **expo-router**: Expo file-based routing system, a layer on top of React Navigation
- **React Query** (= TanStack Query): cache + revalidation library for server requests
- **i18next**: JavaScript translation library, integrated into React via react-i18next
- **DA-XX**: numbered architecture decision (see the dedicated section)
- **Jack**: Ratis mascot, see [[ARCH_feed_jack]]
- **CAB**: cabecoin, see glossary of [[ARCH_RATIS]]
- **BP**: Battlepass, see [[ARCH_REWARDS]]
- **__DEV__**: global React Native variable, `true` in dev, `false` in production — used to keep the dev-bypass login out of the store build
