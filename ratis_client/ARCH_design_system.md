---
type: sub-arch
service: ratis_client
parent: ARCH_CLIENT
related: [ARCH_feed_jack]
status: in-progress
tags: [design-system, ui, components, theme, client, gamification]
updated: 2026-05-09
---

# ratis_client — ARCH Design System

> Mobile Design System: **Duolingo/Clash Royale** pivot confirmed 2026-05-03 (juicy, chunky 3D, coin cascades). Initial Figma teal pattern abandoned. Source of truth: `Ratis Design Pattern v2.html` committed.
> @tags: design-system ui components theme client gamification duolingo clash-royale juicy 3d coin-cascades pattern-v2
> @status: EN-COURS
> @subs: auto

> Parent: [[ARCH_CLIENT]]

> Status: in progress — Duolingo/Clash Royale pivot confirmed 2026-05-03
> Branch: `feature/design-system` (parent branch; sequential PRs `docs/client-design-system-pivot-v1` then `feat/client-design-system-*`)

Artistic direction validated via **Claude Design handoff 2026-05-03** — aesthetic pivot Duolingo / Clash Royale (juicy, chunky 3D, coin cascades). The initial Figma pattern (primary teal palette, Titan One/Bangers fonts) is **abandoned**.

> **Visual source of truth**: `ratis_client/Ratis Design Pattern v2.html` (committed to the repo). Static reference — any implementation divergence must be justified + documented here. The raw handoff bundle (`.design-handoff-2026-05-03/`) is gitignored and serves only as a starting point.

---

## Notable Decisions (pivot 2026-05-03)

- **Retained style**: Duolingo / Clash Royale (saturated colors, hard downward shadows for 3D relief effect, coin cascades, pulsing halos, lively mascot). Abandonment of the initial "medieval / dark fintech" style.
- **Pivoted palette**: terracotta `#DA7756` replaces teal `#00D9B5` as the primary action color. Addition of **jar pink** `#FF6B9D` dedicated to savings (distinct positive emotional charge from gold/CAB).
- **Unified typography**: **Inter only** (weights 400-900 via Google Fonts + `expo-font`). Abandonment of Titan One / Bangers / Space Grotesk — the cost of embedding 3 fontfaces is not justified; Inter 900 + negative letter-spacing is sufficient for title punch.
- **V1 implementation scope** (clarified 2026-05-09 post-shipped reality): **Dashboard + 4 tab screens (Scan, Liste, Produit, Profil)** — all 5 tab screens are in shipped theme v5. See § "V1 Screen Status" below. Initial decision 2026-05-03 ("Dashboard only") superseded by the unified theme v2/v5 refactor documented in `ARCH_scan.md` / `ARCH_liste.md` / `ARCH_product.md` / `ARCH_profil.md` + spec `2026-04-20-screens-theme-v2-design.md`.

### V1 Screen Status

- ✅ Dashboard (theme v5) — DONE
- ✅ Scan (theme v5) — DONE
- ✅ Liste (theme v5) — DONE
- ✅ Produit (theme v5) — DONE
- ✅ Profil (theme v5) — DONE
- ⏳ V2: Achievements panel/modal/toast holographic, Notifications, Shop dedicated screen, Tweaks dev-only panel, Daily spin / lucky box, Customizable avatar (see § "Out of scope V1")

- **Deployment strategy**: **big bang via OTA**, no feature flag. Risk managed by Storybook RN (visual QA pre-merge) + immediate rollback via `eas update:roll-back-to-embedded --channel <name>` (documented procedure § R34 CLAUDE.md).
- **Sequential PR workflow**:
  - **PR1** (this ARCH update) — doc-only
  - **PR2** — `theme.ts` + `animations.ts` + Inter font loading + add `expo-blur` + Storybook RN setup
  - **PR3** — design system primitive components (`Button`, `Card`, `ProgressBar`, `Badge`, `CoinBurst`) + Storybook stories
  - **PR4** — refactored Dashboard using the components + Jack mascot + ROI rings + Jar coin cascade
- **Data hooks preserved**: `use-cab-balance`, `use-missions`, `use-battlepass`, `use-streak`, `use-account-stats` — the refactor changes the UI layer, **not** the data layer. No new backend endpoint required.

---

## Implementation Checklist (V1)

### PR1 — ARCH update doc-only (THIS PR)

- [x] Palette + typography pivot documented
- [x] 4-PR sequential plan
- [x] V1 / V2 / out-of-scope sections segmented
- [x] V1 components documented with props + anatomy
- [x] CSS keyframe animations → Reanimated 4 mapping
- [x] Tech stack + NEW folder structure

### PR2 — Theme + animations + Storybook setup

- [x] `constants/theme.ts` — Colors (bg, surface, terracotta, gold, jar pink, ring colors x10, accents) + Typography (Inter weights, levels 9/11/13/15/22/28) + Spacing + Radii (10/14/20) + Shadows (hard 3D). LegacyColors/Fonts/Design preserved as deprecated for V0 call-sites (use-theme-color, collapsible, roi-rings/missions-card V0) — migrated in PR4.
- [x] `constants/animations.ts` — Durations + EasingPresets (loops jackPulse 2s, roiHaloPulse 2.4s, jarRayspin 14s, etc.; bezier bouncy 0.2,0.9,0.3,1.2). PR3+ components instantiate `Easing.bezier(...EasingPresets.bouncy)` on the worklet side.
- [x] Load Inter 400/500/600/700/800/900 via `expo-font` (`@expo-google-fonts/inter`) + `useDesignSystemFonts` hook consumed at the root layout. Splash gate on `fontsLoaded || fontsError` (graceful system fallback if CDN down).
- [x] `expo-blur ~15.0.8` installed (compatible SDK 54).
- [x] Storybook RN 8.x setup (`@storybook/react-native@^8` + `storybook@^8` + peer `@gorhom/bottom-sheet@^4`) — `.storybook/{main.ts, preview.tsx, index.tsx, storybook.requires.ts, README.md}` + route `app/_storybook.tsx` (lazy-import dev-only via `__DEV__`) + `storybook:generate` script. PR3 will add the first Button/Card/... stories.

> PR2 decision: Storybook 8 rather than 9/10. Reason: v8 is the last stable line with mature docs + ecosystem for RN. v9 just came out (hardening peerDeps), v10 in preview. v8 covers our needs (primitive visualization + states) — bump to v9/10 deferrable to an explicit upgrade if missing features are needed.

### PR3 — Design system primitive components

- [x] `components/design-system/button.tsx` — 4 variants (primary terracotta, secondary outline terracotta, gold claim, danger coral) + Reanimated press scale 0.96 + translateY + haptic Light. 12 Jest tests.
- [x] `components/design-system/card.tsx` — bg `#27293A`, radius 20px, border 1.5px rgba(255,255,255,0.08), hard 3D shadow (`Shadows.card.hard`) + accent variant (border-left 4px + tinted bg + optional cornerGlow position:absolute). 7 Jest tests.
- [x] `components/design-system/progress-bar.tsx` — 4 gradient variants (gold/jarPink/terracotta/cyan), shimmer overlay 2s linear loop, value clamp 0..1, optional label %. 9 Jest tests.
- [x] `components/design-system/badge.tsx` — rarity Common → Legendary (gradients grey/cyan/violet/gold), 3 sizes (sm/md/lg), holo shine sweep overlay (rare+, opt-out via `shine={false}`). 10 Jest tests.
- [x] `components/design-system/coin-burst.tsx` — N gold coins (default 8) radial parabolic explosion + rotation + scale + opacity, 50ms stagger, onComplete via deterministic setTimeout. 6 Jest tests.
- [x] `components/design-system/__stories__/*` — 5 Storybook stories (button/card/progress-bar/badge/coin-burst) with Gallery + individual variants + interactive trigger for coin-burst.
- [x] `components/design-system/index.ts` — re-exports single-import surface (Button, Card, ProgressBar, Badge, CoinBurst + types).

> PR3 decisions:
> - **`Meta`/`StoryObj` not imported from `@storybook/react-native`** — the package does not re-export CSF v3 types (inherited from `@storybook/react` which is not a root dep). Local lightweight `StoryMeta<P>` + `Story<P>` definition, sufficient for `sb-rn-get-stories` which reads `export default` + named exports without inspecting the signature.
> - **Mock `LinearGradient` in tests** — native `expo-linear-gradient` has no `jest-expo` mock. Replaced by a `View` that serializes `colors` in `accessibilityLabel`, allowing palette assertions without coupling tests to the gradient's internal implementation.
> - **`onComplete` of `<CoinBurst />` via JS `setTimeout`** rather than via worklet `runOnJS(onComplete)()` at the end of the last coin — deterministic in tests (fake timers), and avoids tracking the terminal coin on the shared values side.
> - **`secondary` variant added to brief scope** (3→4 variants) because it is already documented in the ARCH § Buttons and needed for PR4 (icon-only outline buttons). Tests + story include all 4.

### PR4 — Refactored Dashboard

- [ ] Header + tabs (`SegmentedTabs` terracotta underline) — V1.1 (existing `DashboardHeaderBar` alias to `AppHeader` conserved for V1)
- [x] `components/dashboard/jack-mascot.tsx` — Jack with `jackPulse` (halo opacity+scale breathing, 2s reversed loop). Inline SVG hamster head + flame badge. 9 Jest tests.
- [ ] `components/dashboard/roi-rings.tsx` — V0 conserved (prestige + fossils domain logic intact). Halo/lightSpin/fossilBlink animations deferred to V1.1 — big-bang rewrite risk not justified.
- [x] `components/dashboard/jar-coin-cascade.tsx` — jar pink + `jarHaloPulse` (2.4s) + `jarRayspin` (14s linear) + `jarSparkle` (4 staggered instances) + `jarCoinFall` (3 staggered instances, Y+rotate+opacity timeline). 11 Jest tests.
- [x] `components/dashboard/mission-card.tsx` — single-mission tile (NEW, distinct from V0 plural `missions-card.tsx`). Daily (orange + gold progress) / Weekly (violet + cyan progress) variants via `<Card variant="accent">` PR3. CAB reward badge + claim banner if `status=completed`. 10 Jest tests.
- [x] `components/dashboard/city-background.tsx` — **Option 1 — Stylized City Map**. Layered SVG (streets, river, buildings, pivoted-palette colored landmarks). Opacity prop clamped [0..1], pointerEvents=none. 6 Jest tests.
- [x] Wire to Dashboard `app/(tabs)/index.tsx` — `CityBackground` replaces `DashboardBackground`, `JackMascot` overlay top-right of ROI hero, new "Monthly Savings" section with `<Card variant="accent" accentColor="jarPink" cornerGlow>` + `JarCoinCascade`. Hardcoded colors replaced by `Colors.*`.
- [x] Jest unit tests (logic) — 36 tests added (9+11+10+6) beyond the 45 existing ones. Storybook visual QA deferred to V1.1 (non-blocking).

> PR4 decisions:
> - **`MissionCard` (singular)** introduced alongside the existing `MissionsCard` (plural) — no rename to avoid breaking the `MissionsBlock` call-site. V0 remains functional; V1.1 will migrate `MissionsBlock` to compose N×`MissionCard`.
> - **`RoiRings` not refactored** — its business logic (prestige levels, fossils, claim flow) is not a V1 essential to rewrite for the aesthetic pivot. The new animations (haloPulse / lightSpin / fossilBlink) would be a pure superset, planned for V1.1 without risk of business regression.
> - **`DashboardHeaderBar` preserved as `AppHeader` alias** — the ARCH targets a clean V1.1 `SegmentedTabs`, but the existing component satisfies V1 needs (CAB balance + season label + missions badge). Refactor deferred.
> - **`MysteryProductCard` / `BattlepassCard` / `JackCard` / `EnrichissementCard` / `MissionsBlock` V0 preserved** — all functional with intact data hooks. Palette + Card primitive migration in V1.1 via targeted small PRs (R33 — prefer several clean PRs over a risky big bang).
> - **Data hooks intact** — `useCabBalance`, `useMissions`, `useBattlepass`, `useStreak`, `useAccountStats`, `useEnrichissement`, `useClaimRing`, `useClaimMission` — no data layer modification.

### PR4.1 — Piggy Bank Pivot (Skia) + SegmentedTabs primitive

**Game design pivot 2026-05-03 (PR4.1)** — `RoiRings` + `JarCoinCascade` legacy REMOVED, replaced by a single Skia-rendered Piggy Bank (mason jar) cycling 5 tier-colors via `prestigeLevel % 5`.

- [x] **Skia 2.6.2 installed** (`@shopify/react-native-skia`, peer-compat RN 0.81 + React 19, no app.json plugin required). Jest passthrough mock for mounting tests `__mocks__/shopify-react-native-skia.tsx`. **⚠️ Native module = mandatory EAS rebuild**, no OTA possible (R34).
- [x] **5 tier colors** (pivot palette): `JarTiers[]` + `getJarTier(prestigeLevel)` exposed from `constants/theme.ts`. Tier 0 terracotta (`#C97D5C` — consistent with DS terracotta) → 1 bronze → 2 copper → 3 silver → 4 gold (`#FFB800`).
- [x] **`components/dashboard/jar-prestige.tsx`** — Skia Canvas (mason jar silhouette + metallic lid + light threading). Vertical gradient liquid fill tier hi→lo + animated sin-wave surface (4s) + glass diagonal highlight. Reanimated 600ms lerp of `currentFill` on prop change. Overlay text % or `totalAbonnements` + EUR below the jar. 10 Jest tests.
- [x] **`components/dashboard/jar-particles.tsx`** — 3 Skia systems: (a) coin drop 3 tier-colored coins at each +1% fill, quadratic gravity + fade-out (1.5s), (b) ambient sparkle 6 gold stars oscillating opacity (1.8s), (c) tier transition burst 24 radial particles 70px (800ms one-shot). Automatic interval cleanup. 7 Jest tests.
- [x] **`components/dashboard/monthly-savings-recap.tsx`** — Typographic text block below the jar: "Ce mois-ci : X€ économisés (Y% du prix de l'abonnement)". No animation, no SVG. 4 Jest tests.
- [x] **`components/design-system/segmented-tabs.tsx`** — Pill-style controlled tabs with sliding terracotta indicator (Reanimated 200ms ease-out). Haptic Light + accessibilityRole tablist/tab + selected state. Measures tabs via onLayout → auto-fit indicator width. 10 Jest tests. Re-exported from `design-system/index.ts`.
- [x] **Storybook stories** — `components/dashboard/__stories__/{jar-prestige,jar-particles,monthly-savings-recap}.stories.tsx` (gallery 5 tiers + interactive triggers) + `components/design-system/__stories__/segmented-tabs.stories.tsx` (2/3/4 tabs + gallery).
- [x] **Wire-up `app/(tabs)/index.tsx`** — Remove `RoiRings` + `JarCoinCascade` imports. Hero replaced by `<JarPrestige size=180 />` + `<JarParticles />` overlay (absolute stack). "Monthly Savings" section keeps `<Card variant=accent jarPink cornerGlow>` but content = `<MonthlySavingsRecap />` (instead of duplicate mini jar). Data hooks unchanged (`useAccountStats` → `computeRings()` extraction). Smoke test `index.test.tsx` adapted.
- [x] **i18n FR** — Added `dashboard.jar.*`, `dashboard.monthly_savings.*`, `dashboard.savings.section_label`/`lifetime_label`.
- [x] **Removal** `roi-rings.tsx` + test (V0 legacy). `utils/roi-rings.ts` (computeRings) **preserved** — pure prestige business logic reused by JarPrestige.
- [x] **Removal** `jar-coin-cascade.tsx` + test.
- [x] **Sticky header v4 refactor** — `AppHeader` (alias `DashboardHeaderBar`) switched back to `#162028` surface + gradient icon buttons + 3D hard shadow. Visual source: `.design-handoff-2026-05-03/project/lib/ratis-real-v4.jsx` AppHeader. testIDs preserved (`header-shop`, `header-missions`, `missions-badge`).
- [x] **v4 card palette refactor** — `battlepass-card` (cyan gradient + reward banner + tiers), `mystery-product-card` (violet surface + cab badge), `enrichissement-card` (amber surface + gold gradient CTA), `jack-card` (coral surface + JackMascot inline avatar). Source: `ratis-real-v4.jsx` (`BattlepassCard` / `MysteryProductCard` / `EnrichissementCard` / `JackCard`). Data hooks + props signatures intact — TODO data wiring documented inline (`PLACEHOLDER_DAYS_REMAINING`, `PLACEHOLDER_SEASON_NUMBER`, `PLACEHOLDER_CAB_REWARD`).
- [x] **`MissionsList` + `SegmentedTabs` daily/weekly** — new component composing `<SegmentedTabs />` + `<MissionCard />` (PR4) with local daily ↔ weekly switch. Empty state "Toutes les missions sont complétées 🎉" + per-tab empty state. Wire-up `app/(tabs)/index.tsx` migrated. `<MissionsBlock />` V0 preserved in parallel (legacy tests `__tests__/components/dashboard/missions-block.test.tsx` still present — V1 PR2 removal when confirmed no consumer references it).
- [ ] **"Prestige break" animation** — V2. V1 ships just tier color change.
- [ ] **Piggy bank / meta-tier Jewels** — V2 (strict YAGNI).

#### TODO data wiring V1 PR2

The v4 components ported in PR4.1 have `// TODO: wire from useFoo()` placeholders for fields not yet exposed by data hooks:

| File | Constant | Expected hook | Field |
|---|---|---|---|
| `battlepass-card.tsx` | `PLACEHOLDER_DAYS_REMAINING` | `useBattlepass()` | `season_end_at` (computed days remaining) |
| `battlepass-card.tsx` | `PLACEHOLDER_SEASON_NUMBER` | `useBattlepass()` | `season_number` (string `"04"`) |
| `battlepass-card.tsx` | `PLACEHOLDER_TOTAL_LEVELS` | `useBattlepass()` | `total_levels` (currently hardcoded 50) |
| `mystery-product-card.tsx` | `PLACEHOLDER_CAB_REWARD` | `useMystery()` (to create) | `cab_reward` of the product of the day |

> No hook will be modified in the PR4.1 scope — V1 PR2 will handle data wiring + new REST endpoints. Components are ready to receive data via their existing props signatures.

> PR4.1 decisions:
> - **Skia 2.6.2** retained (latest stable). No plugin config needed in SDK 54 + new arch already enabled. Bundle size +6MB iOS / +4MB Android (cf Skia docs) — acceptable for the hero visual impact.
> - **Lightweight Skia mock** rather than skipping tests: all Jar components mount in the Node test env via `__mocks__/shopify-react-native-skia.tsx` (passthrough View). The mock exposes `Skia.Path.Make` + `Path.MakeFromSVGString` + `useClock` + `useFrameCallback` no-op, sufficient for 21 green tests in the Jar scope.
> - **JS-side particles timeline (rAF + setInterval)** rather than `useDerivedValue` worklets on the Skia side. Reason: the "functional" cadence (sparkle 200ms tick, coin lifetime 1.5s) does not require worklet frame precision, and the code stays readable + testable. The fill surface sin-wave **stays worklet** as frame cadence is critical for fluidity.
> - **`computeRings()` reused** from `utils/roi-rings.ts` (pure compute) — avoids duplicating prestige business logic. The module keeps its historical name until the pricing is refactored.
> - **v4 card palette refactor deferred** (R33 clean > big-bang): PR4.1 delivers THE game design pivot (Piggy Bank); a follow-up SA will take the 4 cards and the header in a clean palette migration, one PR per card.

### PR4.1.x — Follow-ups (delivered in PR4.1 finalization, 2026-05-03)

- [x] `app-header.tsx` v4 — surface `#162028`, gradient icon buttons + 3D shadow (port `ratis-real-v4.jsx` AppHeader)
- [x] `battlepass-card.tsx` v4 — cyan gradient + days-remaining pill + reward banner + tier tiles (port `ratis-real-v4.jsx`)
- [x] `mystery-product-card.tsx` v4 — violet surface + ? tile + gold cab badge (port `ratis-real-v4.jsx`)
- [x] `enrichissement-card.tsx` v4 — amber surface + bulb 💡 + gold CTA gradient
- [x] `jack-card.tsx` v4 — coral surface + emerald avatar embedding `<JackMascot />`
- [x] `missions-list.tsx` — `<SegmentedTabs />` daily/weekly + map over `<MissionCard />` PR4 (empty state included)
- [x] Storybook stories added: 1 per card (`battlepass-card.stories`, `mystery-product-card.stories`, `enrichissement-card.stories`, `jack-card.stories`, `missions-list.stories`)
- [ ] Storybook stories snapshot tests (pixel diff consistency cross-PRs) — V1 PR2

> PR4.1 finalization decisions:
> - JarPrestige + JarParticles + MonthlySavingsRecap hero **preserved intact** (PR4.1 main ship).
> - No big-bang on component architecture — each card kept its props signature to avoid breaking call-sites + existing tests.
> - `// TODO: wire from useFoo()` placeholders documented in the data-wiring table above.
> - `MissionsBlock` V0 **preserved** (active legacy tests) — V1 PR2 removal after consumer audit.

---

## Index

- [Notable Decisions](#notable-decisions-pivot-2026-05-03)
- [Tech Stack](#tech-stack)
- [Folder Structure](#folder-structure)
- [Colors](#colors)
- [Typography](#typography)
- [Spacing & Layout](#spacing--layout)
- [Components](#components)
- [Animations & Micro-interactions](#animations--micro-interactions)
- [Visual Rewards System](#visual-rewards-system)
- [Backgrounds](#backgrounds)
- [Icons](#icons)
- [States & Feedback](#states--feedback)
- [Foreground Notifications](#foreground-notifications)
- [Personality & What to Avoid](#personality--what-to-avoid)
- [Out of Scope V1](#out-of-scope-v1)

---

## Tech Stack

| Lib | Installed version | Role |
|---|---|---|
| `react-native-reanimated` | `4.1.1` | Native animations (worklets) — base of everything (jackPulse, roiHaloPulse, jarCoinFall…) |
| `react-native-svg` | `15.12.1` | ROI rings, Jack mascot, city map background, badges |
| `react-native-svg-transformer` | `1.5.3` | Direct import of `*.svg` as a React component |
| `expo-font` | `14.0.11` | Loading Inter weights 400-900 |
| `expo-haptics` | `15.0.8` | Tactile feedback on press / claim / level up |
| `react-native-gesture-handler` | `2.28.0` | Card gestures (tap / swipe) |
| `expo-blur` | **to add PR2 (`~15.0.0`)** | Modal overlays + holographic toast (V2 achievements) |
| `@storybook/react-native` | **to add PR2 (`9.x`)** | Component visual QA — compatible Expo SDK 54 |

> No `react-native-skia` or `react-native-canvas` in V1: Reanimated 4 worklets + SVG are sufficient for particles and keyframes from design pattern v2. Reevaluate if V2 (holo achievements + screen confetti + level-up sequence) requires a more powerful canvas.

---

## Folder Structure

```
components/
├─ design-system/         (NEW — generic primitives, feature-agnostic)
│  ├─ button.tsx
│  ├─ card.tsx
│  ├─ progress-bar.tsx
│  ├─ badge.tsx
│  ├─ coin-burst.tsx
│  ├─ __stories__/        (Storybook stories — one .stories.tsx per primitive)
│  │  ├─ button.stories.tsx
│  │  ├─ card.stories.tsx
│  │  └─ ...
│  └─ index.ts            (re-exports — single import surface)
├─ dashboard/             (NEW — Dashboard-specific components)
│  ├─ jack-mascot.tsx
│  ├─ roi-rings.tsx
│  └─ jar-coin-cascade.tsx
├─ themed-text.tsx        (existing — untouched V1)
├─ themed-view.tsx        (existing — untouched V1)
├─ scan/                  (existing — V2 refactor)
├─ liste/                 (existing — V2 refactor)
└─ ...
```

> Rule: a **generic** reusable component (Button, Card, ProgressBar) → `design-system/`. A component **specific** to a feature/screen (JackMascot, ROIRings) → feature subfolder (`dashboard/`, later `liste/`, `profil/`…).

---

## Colors

```ts
// constants/theme.ts — pivoted palette v2 (2026-05-03)

export const Colors = {
  // Backgrounds
  bg:        '#1a2428',  // Screen background — NEVER anything else
  surface:   '#27293A',  // Cards / standard surfaces
  overlay:   '#0F1419',  // Modals / bottom sheets

  // Semantic roles (max 2 accents per screen)
  terracotta:    '#DA7756',  // Primary action — CTA, scan navbar, optimiser
  terracottaHi:  '#E8896A',  // Button gradient top (180deg, #E8896A → #DA7756)
  terracottaLo:  '#A8562E',  // Primary button border
  terracottaSh:  '#6B3218',  // Hard 3D shadow (0 4px 0)

  gold:          '#FFB800',  // Claim / reward — XP, +CAB, prices
  goldHi:        '#FFE066',  // Gradient top (180deg, #FFE066 → #FFB800)
  goldLo:        '#B47800',  // Border
  goldSh:        '#7E5300',  // Hard shadow

  jarPink:       '#FF6B9D',  // Savings — positive emotional (jar / list total / route)
  jarPinkHi:     '#FF8FB3',  // Gradient top
  jarPinkBg1:    '#2A1A1A',  // Savings card (gradient 160deg)
  jarPinkBg2:    '#1F1212',

  // Secondary accents (per feature, never 2 on the same screen)
  violet:        '#A78BFA',  // Weekly missions
  violetText:    '#C4B5FD',
  orange:        '#FF6B35',  // Daily missions, Jack streak
  orangeText:    '#FFB89D',
  cyan:          '#0EA5E9',  // Scan fullscreen, battlepass header
  cyanText:      '#67E8F9',
  amber:         '#F59E0B',  // Season, progression
  amberText:     '#FCD34D',
  coral:         '#EF4444',  // Alerts, reset, danger
  coralText:     '#FCA5A5',

  // Text
  textPrimary:   '#FFFFFF',
  textSecondary: 'rgba(255,255,255,0.45)',
  textTertiary:  'rgba(255,255,255,0.30)',
  textMuted:     'rgba(255,255,255,0.40)',
} as const;

// ROI rings — 10 cycled colors (gaming Duolingo style, dashboard hero)
export const RingColors = [
  '#22D3EE',  // 1  cyan
  '#2DD4BF',  // 2  teal
  '#34D399',  // 3  green
  '#A3E635',  // 4  lime
  '#FACC15',  // 5  yellow
  '#FBBF24',  // 6  amber
  '#F97316',  // 7  orange
  '#EF4444',  // 8  red
  '#EC4899',  // 9  pink
  '#A855F7',  // 10 purple
] as const;

// Reward tiers (badges / achievements)
export const RewardTiers = {
  bronze:   ['#CD7F32', '#8B4513'],
  silver:   ['#C0C0C0', '#808080'],
  gold:     ['#FFD700', '#FFA500'],
  platinum: ['#E5E4E2', '#B0C4DE'],
  diamond:  ['#FF6B9D', '#A855F7'],  // gradient jar pink → purple
} as const;

// Rarity badges (V2 achievements — but palette set from V1)
export const Rarity = {
  common:    'rgba(255,255,255,0.40)',
  rare:      '#22D3EE',  // cyan
  epic:      '#A855F7',
  legendary: '#FFB800',  // + holo shine overlay
} as const;
```

### Cardinal Rules (from design pattern v2)

- Screen background always **`#1a2428`** — never anything else.
- All **prices in gold** (`#FFB800`).
- All **savings in jar pink** (`#FF6B9D`).
- Primary CTA always full **terracotta**.
- **Max 2 accent** colors per screen (otherwise visually saturated).
- Hard shadows always pointing **downward** (never lateral — preserves the Clash Royale 3D relief effect).
- Background images: opacity `0.15`, blend `luminosity`.

---

## Typography

**Inter only** — weights 400, 500, 600, 700, 800, 900 loaded via `expo-font` (Google Fonts).

| Level | Size | Weight | Letter-spacing | Usage |
|---|---|---|---|---|
| LABEL | 9px | 800 | +0.8 | Categories, units, section labels (uppercase) |
| Body small | 11px | 600 | 0 | Secondary text, descriptions |
| Item title | 13px | 800 | -0.2 | List items, product names |
| Card title | 15px | 900 | -0.3 | Main titles inside cards |
| Hero value | 22px | 900 | -0.6 | Totals, hero values (`22,40€`) |
| Metric XXL | 28px | 900 | -1.2 | ROI rings metrics, screen titles |

```ts
export const Typography = {
  label:    { fontFamily: 'Inter_800ExtraBold', fontSize: 9,  letterSpacing: 0.8,  textTransform: 'uppercase' },
  bodySm:   { fontFamily: 'Inter_600SemiBold', fontSize: 11, letterSpacing: 0 },
  itemTitle:{ fontFamily: 'Inter_800ExtraBold', fontSize: 13, letterSpacing: -0.2 },
  cardTitle:{ fontFamily: 'Inter_900Black',     fontSize: 15, letterSpacing: -0.3 },
  hero:     { fontFamily: 'Inter_900Black',     fontSize: 22, letterSpacing: -0.6 },
  metric:   { fontFamily: 'Inter_900Black',     fontSize: 28, letterSpacing: -1.2 },
} as const;
```

> No global 3D text-shadow: replaced by physical shadows on container components (cards, buttons). Hero values keep their punch via weight 900 + negative letter-spacing.

---

## Spacing & Layout

Base unit: **4px**

```ts
export const Spacing = {
  xs:  4,
  sm:  8,
  md:  12,
  lg:  16,
  xl:  24,
  xxl: 32,
} as const;

export const Radii = {
  icon:   10,  // icon in secondary button / internal chips
  badge:  8,   // badges, pills, rules
  btn:    14,  // buttons (primary + secondary)
  btnSm:  12,  // gold claim button (sm size)
  card:   20,  // cards (standard + accent)
  modal:  24,  // bottom sheets
} as const;

export const Shadows = {
  // Clash Royale 3D effect — hard downward shadow + diffuse shadow + inset top highlight
  card: {
    // 0 5px 0 rgba(0,0,0,0.35), 0 12px 22px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.08)
    hard:    { shadowOffset: {width:0,height:5},  shadowRadius:0,  shadowOpacity:0.35, elevation:5  },
    diffuse: { shadowOffset: {width:0,height:12}, shadowRadius:22, shadowOpacity:0.40, elevation:8  },
    insetTop: 'rgba(255,255,255,0.08)',  // simulated via a line / top overlay in RN
  },
  buttonPrimary: {
    // 0 4px 0 #6B3218, inset 0 1px 0 rgba(255,255,255,0.35)
    hard:     { shadowOffset: {width:0,height:4}, shadowRadius:0, shadowOpacity:1, shadowColor:'#6B3218', elevation:4 },
    insetTop: 'rgba(255,255,255,0.35)',
  },
  buttonClaim: {
    hard:     { shadowOffset: {width:0,height:3}, shadowRadius:0, shadowOpacity:1, shadowColor:'#7E5300', elevation:3 },
    insetTop: 'rgba(255,255,255,0.40)',
  },
} as const;
```

> In RN, multi-layer CSS `box-shadow` is not native. To reproduce the hard+diffuse+inset stacking, combine: root `View` with hard `shadowOffset` (Android `elevation`) + inner `View` with 1px white border-top (simulates `inset 0 1px 0`) + optional outer wrapper for the wider diffuse. Documented in detail in `card.tsx` (PR3).

---

## Components

### Buttons — 3 roles

**Primary (terracotta)** — main action, fullWidth CTA
```
background:   linear-gradient(180deg, #E8896A → #DA7756)
border:       2px solid #A8562E
borderRadius: 14
shadow:       0 4px 0 #6B3218  (hard 3D downward shadow)
insetTop:     0 1px 0 rgba(255,255,255,0.35)  (highlight)
text:         13px, 900, white, letterSpacing -0.1
padding:      11px 16px
pressed:      translateY(4px) + shadow reduced to 0  (physical press-down)
```

**Secondary (outline terracotta)** — available non-urgent option, often icon-only
```
background:   transparent
border:       2px solid #DA7756
borderRadius: 14
shadow:       0 4px 0 rgba(100,40,20,0.5)  (same 3D relief)
insetTop:     0 1px 0 rgba(218,119,86,0.15)
text/icon:    color #DA7756
size icon:    44×44 (iOS tap target)
pressed:      translateY(4px) + reduced shadow
```

**Gold / Claim** — reward to collect (XP missions, +CAB, prices)
```
background:   linear-gradient(180deg, #FFE066 → #FFB800)
border:       2px solid #B47800
borderRadius: 12  (sm size = reduced radius)
shadow:       0 3px 0 #7E5300
insetTop:     0 1px 0 rgba(255,255,255,0.40)
text:         11px, 900, color #3A2200  (dark text on light background)
padding:      8px 12px
```

**Danger (coral)** — irreversible actions (reset, delete)
```
background:   linear-gradient(180deg, #FCA5A5 → #EF4444)
border:       2px solid #B91C1C
borderRadius: 14
shadow:       0 4px 0 #7F1D1D
press hover:  horizontal shake 2px / 150ms
```

#### Expected props `<Button />`

```ts
type ButtonProps = {
  variant: 'primary' | 'secondary' | 'gold' | 'danger';
  size?: 'sm' | 'md';                  // md by default, sm for claim
  fullWidth?: boolean;
  iconOnly?: boolean;                  // 44×44 square
  leftIcon?: ReactNode;
  rightIcon?: ReactNode;
  onPress: () => void;
  disabled?: boolean;
  loading?: boolean;
  children?: ReactNode;
  testID?: string;
};
```

---

### Cards

**Standard**
```
background:    #27293A
border:        1.5px solid rgba(255,255,255,0.08)
borderRadius:  20
shadow:        0 5px 0 rgba(0,0,0,0.35), 0 12px 22px rgba(0,0,0,0.4)
insetTop:      0 1px 0 rgba(255,255,255,0.08)
padding:       16
```

**Accent (savings — jar pink)**
```
background:    linear-gradient(160deg, #2A1A1A → #1F1212)
border:        1.5px solid rgba(255,107,157,0.35)
borderRadius:  20
shadow:        0 5px 0 rgba(80,20,40,0.7), 0 12px 22px rgba(0,0,0,0.4)
insetTop:      0 2px 0 rgba(255,255,255,0.08)
cornerGlow:    radial-gradient(closest-side, rgba(255,107,157,0.20), transparent 70%) absolute position top:-20 right:-20 80×80
```

#### Expected props `<Card />`

```ts
type CardProps = {
  variant?: 'standard' | 'accent';
  accentColor?: 'jarPink' | 'gold' | 'terracotta' | 'violet' | 'orange' | 'cyan';  // if accent
  cornerGlow?: boolean;              // enables the radial gradient corner
  padding?: number;                  // override default 16
  children: ReactNode;
  onPress?: () => void;              // if tappable, adds pressedScale 0.98
  testID?: string;
};
```

---

### Progress Bars

```
height:        12-16px (never < 10)
borderRadius:  full (height/2)
background:    #1a2428 + border 2px rgba(255,255,255,0.08)
fill:          animated gradient by context (gold for XP, jarPink for savings)
shimmer:       translucent overlay scrolling left→right in a loop (Reanimated withRepeat)
nearFull:      external accent-color glow overflow (shadowOpacity ↑ when value ≥ 0.95)
```

#### Props `<ProgressBar />`

```ts
type ProgressBarProps = {
  value: number;                    // 0..1
  variant: 'gold' | 'jarPink' | 'terracotta' | 'cyan';
  height?: number;                  // 12 by default
  shimmer?: boolean;                // true by default
  showGlowAtFull?: boolean;         // true by default
};
```

---

### Badges (rarity system — V1 palette set, V2 usage)

```
Common:    rgba(255,255,255,0.40)  — no effect
Rare:      #22D3EE                 — subtle glow
Epic:      #A855F7                 — glow + slow pulse
Legendary: #FFB800 + rainbow holo  — shine sweep on unlock
```

All: 3-4px border, background gradient, strong shadow. Unlock animation = `achHoloShine` (background-position loop, 2s) + `achUnlockSlideIn` (entry).

---

### CoinBurst (coin collect)

Sequence (Reanimated worklet):
1. **Scale bounce**: 0.8 → 1.2 → 1.0 (duration 400ms, withSequence + withTiming easing-out)
2. **Rotation**: 0deg → 360deg (duration 500ms, ease-out)
3. **Gold particles**: 8-12 SVG circles that explode radially with 30ms stagger (positions calculated in the UI thread worklet)

Triggered on XP claim / +CAB / validated referral. Haptic feedback `light` + sound (V2).

---

### Dashboard-specific Components (V1)

#### `<JackMascot />` (`components/dashboard/jack-mascot.tsx`)

The hamster mascot — embodies engagement and the streak. `jackPulse` animation (boxShadow breathing glow alternating between `0 0 28px rgba(255,107,53,0.32)` and `0 0 44px rgba(255,107,53,0.55)`, duration 2s, infinite).

Props:
```ts
type JackMascotProps = {
  streakDays?: number;             // displays flame badge if > 0
  state?: 'idle' | 'happy' | 'sleepy';  // V1 = idle; happy/sleepy = V2
  onPress?: () => void;            // tap → feed jack flow
};
```

#### `<RoiRings />` (`components/dashboard/roi-rings.tsx`)

The Dashboard hero — N concentric rings (up to 10), each ring = a ROI category (cashback / streaks / battlepass / referral / …). Animations:
- `roiHaloPulse` — halo behind each ring (opacity + scale, 2.4s, infinite)
- `roiLightSpin` — luminous gradient spinning around the active ring (3.2s linear infinite)
- `roiFossilBlink` — fossils (markers) along the ring blinking in staggered cascade

Props:
```ts
type RoiRingsProps = {
  rings: Array<{
    label: string;
    progress: number;             // 0..1
    color: typeof RingColors[number];
    fossilCount?: number;         // markers; default auto scale
  }>;
  centerLabel?: string;           // center value (e.g.: "3,2")
  onRingPress?: (idx: number) => void;
};
```

#### `<JarCoinCascade />` (`components/dashboard/jar-coin-cascade.tsx`)

The savings jar — displays the total `-X,XX€` saved, with layered animations:
- `jarHaloPulse` — pulsing pink halo behind (2.4s)
- `jarRayspin` — radial rays spinning (14s linear)
- `jarSparkle` — ✨ sparkles appearing/disappearing in stagger (4-6 instances, durations 5-7s)
- `jarCoinFall` — 🪙 coins falling from the top (3 instances, durations 3-4s, stagger)

Props:
```ts
type JarCoinCascadeProps = {
  totalSavingsCents: number;      // displayed formatted `-X,XX€`
  coinCount?: 3;                  // number of falling coins (default 3)
  sparkleCount?: 4;               // default 4
};
```

---

## Animations & Micro-interactions

**Retained lib**: `react-native-reanimated@4.1.1` — already installed. Everything runs in the UI thread worklet (`useSharedValue` + `withTiming` / `withRepeat` / `withSequence`).

```ts
export const Durations = {
  instant: 100,   // tactile feedback (pressed)
  fast:    200,   // UI transitions
  normal:  300,   // max for page transitions
  slow:    500,   // celebrations (coin collect, level up)
  loop:    {
    jackPulse:      2000,   // 2s
    roiHaloPulse:   2400,
    roiLightSpin:   3200,
    roiFossilBlink: 1600,   // per fossil, 0.4s stagger
    jarHaloPulse:   2400,
    jarRayspin:     14000,
    jarSparkle:     5500,   // average; 5-7s per instance
    jarCoinFall:    3500,   // average; 3-4s per instance
  },
} as const;

export const Easings = {
  out:      Easing.out(Easing.cubic),
  inOut:    Easing.inOut(Easing.cubic),
  bouncy:   Easing.bezier(0.2, 0.9, 0.3, 1.2),  // slideUp pattern v2
  linear:   Easing.linear,
} as const;
```

### CSS Keyframes → Reanimated 4 Mapping

All keyframes from design pattern v2 (HTML) must be ported to RN. Porting pattern:

| CSS Keyframe | Reanimated 4 Target | Notes |
|---|---|---|
| `toastIn` (translateX -50% + Y 8→0, opacity 0→1, 250ms) | `useSharedValue` y=8, opacity=0 → `withTiming({ y:0, opacity:1 }, { duration: 250 })` | Wrapper `Animated.View` + `transform: [{ translateY: y.value }]` |
| `fadeIn` (opacity 0→1, 200ms) | `withTiming(1, { duration: 200 })` on opacity | trivial |
| `slideUp` (Y 100%→0, 260ms bezier(.2,.9,.3,1.2)) | `withTiming(0, { duration: 260, easing: Easings.bouncy })` | Modal entry |
| `jackPulse` (alternating boxShadow, 2s infinite) | `withRepeat(withTiming(1, { duration: 1000 }), -1, true)` → interpolate on shadowOpacity / shadowRadius | RN does not support direct `shadowRadius` animation on Android — use an absolute-layered `View` halo underneath + animate its `opacity` + `scale` |
| `roiHaloPulse` (opacity .55→1, scale 1→1.18, 2.4s) | `withRepeat(withSequence(withTiming opacity, scale), -1, true)` on halo `Animated.View` | Separate halo pattern, classic |
| `roiLightSpin` (rotate 0→360, 3.2s linear) | `withRepeat(withTiming(360, { duration: 3200, easing: linear }), -1, false)` | Continuous rotation, **not reversed** |
| `roiFossilBlink` (opacity base→1 + drop-shadow, 0.4s/fossil) | Map on each fossil: `withRepeat(withTiming(1, { duration: 200 }), -1, true)` + delay calculated as `i * 400 / N` | stagger via `withDelay` |
| `jarHaloPulse` | same as `roiHaloPulse` (different parameter values) | |
| `jarRayspin` (rotate 14s linear) | `withRepeat(withTiming(360, { duration: 14000, easing: linear }), -1, false)` | SVG rays in rotating wrapper |
| `jarSparkle` (opacity 0→1→0, scale 0.6→1.1→0.6, 5-7s) | `withRepeat(withSequence(...), -1, false)` on opacity + scale, random delay per instance | non-reversed to get the full arc |
| `jarCoinFall` (Y 0→60, rotate 0→280, opacity timeline) | `withRepeat(withTiming({ y, rotate }, { duration: 3500 }), -1, false)` + random delay | Reset at each cycle |
| `achHoloShine` (background-position, 2s) — V2 | gradient + animated mask via `react-native-linear-gradient` + `react-native-svg` mask, or `MaskedView` | V2 only |
| `achUnlockSlideIn`, `achBurstSpin`, `achIconPop` — V2 | Stagger `withSequence` on the achievement toast | V2 only |

### Page Transitions

- Lateral slide + fade scale 0.95 → 1, max 300ms (Durations.normal).
- Hard rule: **no transition > 300ms outside celebrations**.

### Tactile Feedback (all interactive elements)

- Pressed: `scale(0.95)` instant (100ms) + `expo-haptics` `Light`
- Success: bounce + green flash (200ms) + haptics `Success`
- Error: horizontal shake + red flash (200ms) + haptics `Warning`
- Loading: pulse + rotate

### Celebrations

- **Coin collect**: `<CoinBurst />` (see component)
- **Level up (V2)**: white screen flash 100ms → confetti explosion → badge elastic grow → optional sound
- **Streak > 7d**: shake + color pulse + fire particles around Jack
- **Combo counter (V2)**: x2/x3/x5 shake + scale up, disappears after 1.5s

---

## Visual Rewards System

### Tier gradients (V2 achievements — palette set from V1)

```ts
bronze:   linear-gradient(135deg, #CD7F32, #8B4513)
silver:   linear-gradient(135deg, #C0C0C0, #808080)
gold:     linear-gradient(135deg, #FFD700, #FFA500)
platinum: linear-gradient(135deg, #E5E4E2, #B0C4DE)
diamond:  linear-gradient(135deg, #FF6B9D, #A855F7)  // jar pink → purple
```

### Celebration animation (unlock — V2)

1. Star burst from center
2. Confetti rain
3. Expanding ring glow pulse
4. Subtle screen shake (2px, 3 oscillations)
5. Coin fountain (optional)

---

## Backgrounds

All options are **textures/patterns as overlays** on `#1a2428`. Target opacity 3-8%.

| # | Name | Mood | Ratis Consistency | Status |
|---|---|---|---|---|
| **1 ⭐** | **Stylized City Map** | Urban, clever | ★★★★★ | **V1 retained** — Figma favorite + design pattern v2 |
| 2 | Connected Dot Network | Tech, smart | ★★★★☆ | V2 backlog |
| 3 | Isometric Gaming Grid | Video game, fun | ★★★★☆ | V2 backlog |
| 4 | Diagonal Speed Lines | Street, dynamic | ★★★☆☆ | V2 backlog |
| 5 | Urban Grunge Texture | Street, underground | ★★★☆☆ | V2 backlog |
| 6 | Pixelated Retro | 8-bit nostalgia | ★★☆☆☆ | V2 backlog (celebration screens only) |

### Option 1 — Stylized City Map ⭐ (V1)

Gaming GPS style — irregular street grid lines, bright dots = shops, optimal route line that pulses.

```
Elements:
- Irregular street grid (SVG) — stroke #FFFFFF opacity 4%
- Bright dots at intersections (shops) — terracotta opacity 15-20%, radius 3-4px
- Optimal route line — terracotta dashed, looping dashoffset animation (pulse)
- Glow halo on active dots

React Native implementation:
- Static SVG for the grid (react-native-svg)
- Reanimated for the route line (dashoffset loop)
- Low zIndex, pointerEvents:'none' — purely decorative
```

> Options 2-6 (formerly detailed in V0) remain available in git history if V2 reintroduction is needed. Not re-developed here to avoid bloating the active V1 ARCH.

---

## Icons

Style: **duotone** with accent colors. 2px stroke. Filled = primary actions, Outline = secondary.

| Pack | Usage |
|---|---|
| Lucide (already installed) | Keep for existing compatibility |
| Phosphor Icons | Duotone — preferred for gamified look |
| Remix Icon | Fun — rewards & missions |

```ts
export const IconSizes = {
  xs: 16,
  sm: 20,
  md: 24,  // default
  lg: 32,
  xl: 48,
} as const;
```

---

## States & Feedback

| State | Behavior |
|---|---|
| Default | opacity 100% |
| Hover / Focus (web only) | scale 1.05 + brightness 1.1 |
| Pressed | scale 0.95 instant (100ms) + translateY 4px on 3D buttons |
| Disabled | opacity 40% + grayscale |
| Loading | pulse + shimmer |
| Success | green flash (#34D399) + bounce + haptics Success |
| Error | red flash (coral) + shake + haptics Warning |

---

## Foreground Notifications

When the app is in the foreground, certain notifications should not appear as alerts — the UI updates silently.

```ts
Notifications.setNotificationHandler({
  handleNotification: async (notification) => {
    const type = notification.request.content.data?.type;
    const currentScreen = navigationRef.current?.getCurrentRoute()?.name;

    if (type === 'route_ready' && currentScreen === 'RouteDetail') {
      const routeId = notification.request.content.data?.route_id;
      routeStore.refresh(routeId);
      return { shouldShowAlert: false, shouldPlaySound: false, shouldSetBadge: false };
    }
    return { shouldShowAlert: true, shouldPlaySound: true, shouldSetBadge: true };
  },
});
```

**Rule**: if the user is already on the relevant screen → silent UI refresh. Otherwise show the notification (or custom in-app banner). The backend changes nothing.

---

## Personality & What to Avoid

**Ratis should be:** energetic (constant movement, pulsing halos), playful (coins/badges/levels everywhere), exciting (saturated colors, strong contrasts), motivating (visible progression, 3D celebrations), urban (dark, edgy, not childish), **juicy** (presses that indent, particles that burst — Duolingo-tier).

| ❌ To avoid | ✅ To do |
|---|---|
| Soft pastels | Saturated colors (terracotta, gold, jar pink) |
| Extreme minimalism | Visible gamified elements (halos, fossils, sparkles) |
| Corporate / fintech style | Gaming style Duolingo / Clash Royale |
| White or very light background | Strict dark background `#1a2428` |
| Slow animations (>300ms outside celebrations) | Immediate feedback (<150ms), celebrations 400-500ms |
| Glassmorphism (transparent backdrop-blur) | Solid `#27293A` cards + hard shadows + inset highlight |
| More than 2 accents per screen | Max 2 — absolute discipline |
| Lateral shadows | Always downward (3D relief) |

---

## Out of Scope V1

### V2 Components (future tab / feature refactors)

- ~~**Liste / Scan / Produit / Profil** refactored — V2 (V1 = Dashboard only)~~ — **superseded 2026-05-09**: the 4 tab screens were refactored to theme v5 and shipped in V1. See § "V1 Screen Status" at the top of the ARCH.
- **Achievements**: panel + detail modal + holographic toast (`achHoloShine`, `achUnlockSlideIn`, `achBurstSpin`, `achIconPop`)
- **Tweaks panel** (dev-only) — V2

### Backgrounds backlog

- Options 2-6 (Dot network, Iso grid, Speed lines, Grunge, Pixelated) — V2 if option 1 does not hold across all screens

### Non-priority effects

- **Daily spin wheel / lucky box** — engagement loop, V2
- **Avatar / Customizable Character** — to design with cosmetics system (PROD_CHECKLIST)
- **Sound effects** — implementable but not a priority; plan a toggle-off
- **Crack effect** danger button — optional V2
- **Coin fountain** celebration — optional V2
- **Canvas particles** (full system) — Reanimated worklets are sufficient in V1
- **Visual snapshot tests** — Storybook RN (manual visual QA) is sufficient for V1, snapshots = V2

### Test Strategy

- V1: Jest unit tests (component logic — props, callbacks, state) + Storybook RN (manual visual QA pre-merge).
- V2: add snapshot tests (`react-test-renderer`) + screenshot regression (`detox` or similar) if component base is stabilized.
