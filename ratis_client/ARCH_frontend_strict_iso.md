---
type: arch
service: ratis_client
status: planned
parent: ARCH_design_system
related: [ARCH_design_system, ARCH_scan_history]
tags: [frontend, design-fidelity, claude-design, ratis_client, expo, react-native]
business_domain: design
rgpd_concern: false
updated: 2026-05-04
---

# ratis_client — ARCH Frontend strict iso Claude Design

> "Strict iso" rule from the Claude Design handoff: reproduce visually identically what is in the handoff bundle. No visual simplification, no invented components, no undocumented variants. Implementation dispatched to SA Dev + audit by SA Reviewer.
> @tags: frontend design-fidelity claude-design ratis_client expo react-native strict-iso handoff design-system pixel-perfect planned 2026-05-04
> @status: PLANIFIÉ
> @subs: auto

> Parent : [[ARCH_design_system]] · Relations : [[ARCH_scan_history]]

> Status: 📋 **Planned** — design validated 2026-05-04 (orchestrator + product owner). Implementation dispatched to a SA Dev afterwards, code audit by a SA Reviewer after that.

> **Genesis** — PR4.1 #285 delivered a refactored Dashboard as a free interpretation of the Claude Design handoff. The PO noted that **the result is not isomorphic** to the source design. This ARCH formalizes the "stricto sensu" rule: what is in the handoff MUST be in the app, and nothing else.

---

## Mission (immutable)

> **Reproduce visually, in the Ratis app (Expo / React Native), exactly what is in the Claude Design handoff provided by the product owner. No visual simplification without explicit agreement. No invention of components or variants. No "I found something nicer".**

That's it. **Out of scope**: product decisions / data flow / business logic — all of that remains governed by the existing ARCHs. This ARCH talks ONLY about visual fidelity.

---

## Single source of truth

### ABSOLUTE visual authority — V5 screenshots

```
ratis_client/Ratis_handoff/screenshots/V5-FINAL-iso/
├── Accueil-haut.png          # Dashboard top half
├── Accueil-bas.png           # Dashboard bottom half
├── Liste Courses.png         # Liste tab — onglet "Liste"
├── Liste itineraire.png      # Liste tab — onglet "Itinéraire"
├── Produit.png               # Fiche produit
├── Profil.png                # Profil tab
├── Mission popup.png         # Modal missions
└── Succès.png                # Modal achievements
```

**In case of divergence between the JSX and the screenshots, the screenshots are authoritative.** The PO validated each PNG as the target visual render.

### Code reference — JSX handoff (secondary)

```
ratis_client/Ratis_handoff/lib/
```

The folder contains the **canonical** JSX iterations retained after the purge of successive "Claude Design attempts" (cf PR `chore/handoff-purge-legacy` 2026-05-10): `ratis-real-v4.jsx`, `ratis-other-tabs.jsx`, `ratis-liste.jsx` (+ `liste-data.jsx`, `liste-ui.jsx`), `ratis-achievements-ui.jsx` (+ `achievements-data.jsx`), `ratis-screens.jsx`, `roi-variants.jsx`. The canonical selection is made by mapping from the screenshots `screenshots/V5-FINAL-iso/` (cf § V1 Scope below).

Usable assets:
- `lib/ratis-spring-scene.png` (BattlepassCard background, an asset that PR4.1 had not carried over)
- `lib/cabecoin-chest.svg`
- `lib/jack-mascot.svg`
- `lib/market.svg`

### Out of scope

- `app/scan-history.tsx` (scan history) **was not designed** by Claude Design. Remains at current V0.
- `app/(auth)/login.tsx`, `app/my-info.tsx`, `app/referral.tsx`: **not covered by V5 screenshots**, remain at current V0 (to be designed in V2 if the PO wishes).
- `app/(tabs)/scan.tsx`: may remain at current V0 or be freely adapted — PO indifferent, will be reworked separately.

---

## Golden rule — "stricto sensu"

1. **Every component visible in the source JSX MUST be present and visually faithful in the app.**
2. **Every referenced asset MUST be ported to RN** (or a substitution validated by the PO in the commit message).
3. **Every animation present MUST be ported 1:1** (framer-motion → Reanimated equivalents).
4. **Any divergence is forbidden EXCEPT**:
   - Documented technical RN constraint (e.g.: no equivalent for CSS X on mobile)
   - Explicitly validated by the PO in the PR description with rationale

5. **Not pixel-perfect** — tolerance of ±2-4px on spacing given RN multi-device constraints (different PPI, iOS safe-areas, dynamic island, etc.). But **visually very close** = mandatory.

---

## Web → React Native mapping (reference)

| Web (handoff) | React Native (target) |
|---|---|
| `<div className="bg-X p-Y">` | `<View style={{ backgroundColor: tokens.X, padding: tokens.Y }}>` |
| `<span>`, `<p>`, `<h1>-<h6>` | `<Text style={...}>` |
| Tailwind colors (`bg-cyan-700`, `text-amber-300`) | `Colors.*` from `constants/theme.ts` |
| `framer-motion` | `react-native-reanimated` |
| `<motion.div whileHover>` | **ignored** (no hover on mobile) |
| `<motion.div whileTap>` | `<Pressable>` + Reanimated press scale |
| `<motion.div initial animate transition>` | `useSharedValue` + `useAnimatedStyle` + `withTiming/withSpring` |
| `<motion.div variants>` | Reanimated equivalent + `withSequence` |
| `<AnimatePresence>` | `Animated.View entering exiting` (Layout Animation Reanimated) |
| Tailwind gradients (`bg-gradient-to-br from-X to-Y`) | `expo-linear-gradient` `<LinearGradient colors={[X, Y]} start end>` |
| `backdrop-blur-md`, `backdrop-blur-lg` | `expo-blur` `<BlurView intensity={...}>` |
| SVG inline | `react-native-svg` `<Svg>` `<Path>` etc. |
| `@shopify/react-native-skia` | already installed (PR4.1) — for advanced effects (gauges, particles) |
| `<img src="...">` | `<Image source={require(...)} />` (bundled assets) |
| `<button onClick>` | `<Pressable onPress>` |
| `<input>` | `<TextInput>` |
| `position: fixed` | `position: 'absolute'` (no fixed in RN) |
| `position: sticky` | `<Animated.ScrollView stickyHeaderIndices>` or Reanimated pattern |
| CSS `transition` | `withTiming` Reanimated |
| Hover states | Touch states (`pressed`) via `Pressable` |
| Cursor pointer | (ignored) |
| `z-index` | `zIndex` (but beware different RN behavior per layer) |
| Heavy box-shadow | `expo-shadow` or approximation via 2-layer pattern |

---

## V1 Scope — 4 screens + 2 modals

The V1 iso covers the screens present in the V5-FINAL-iso screenshots (absolute visual authority).

| Ratis Screen | V1 iso Status | V5 Screenshot | Canonical handoff JSX |
|---|---|---|---|
| `app/(tabs)/index.tsx` (Dashboard) | **To iso** | `Accueil-haut.png` + `Accueil-bas.png` | TBD via Explore SA STAGE 1 mapping |
| `app/(tabs)/liste.tsx` (Liste) | **To iso** | `Liste Courses.png` (Liste tab) + `Liste itineraire.png` (Itinéraire tab) | TBD via Explore SA STAGE 1 mapping |
| `app/(tabs)/produit.tsx` (Produit) | **To iso** | `Produit.png` | TBD via Explore SA STAGE 1 mapping |
| `app/(tabs)/profil.tsx` (Profil) | **To iso** | `Profil.png` | TBD via Explore SA STAGE 1 mapping |
| Mission modal (popup) | **To iso** | `Mission popup.png` | TBD via Explore SA STAGE 1 mapping |
| Achievements modal | **To iso** | `Succès.png` | TBD via Explore SA STAGE 1 mapping |
| `app/(tabs)/scan.tsx` | **Flexible** (V0 OK or freely adapted) | (not in V5) | — PO indifferent, to be reworked separately |
| `app/(auth)/login.tsx` | Out of V1 | (not in V5) | Remains V0 |
| `app/my-info.tsx` | Out of V1 | (not in V5) | Remains V0 |
| `app/referral.tsx` | Out of V1 | (not in V5) | Remains V0 |
| `app/scan-history.tsx` | Out of V1 | (not in V5) | Remains V0, PO confirms |

### Hero decision (Q-Z + Q-AB 2026-05-04)

The Dashboard hero is the **JarPrestige** (delivered PR4.1, Skia). The old ROI rings are **definitively abandoned**. If a handoff JSX still shows the rings, it is an obsolete iteration.

**Finalized V1 hero spec (product concept)**:

- **Single shape**: 1 SVG jar (no pig, no crown — V2)
- **Fill calculation**:
  ```
  fill_pct = (current_period_savings_cents / monthly_subscription_price_cents) × 100
  ```
- **Prestige**: when `fill_pct ≥ 100%` → `prestigeLevel += 1`, fill resets to 0, next tier color
- **Color tiers** (cycle of 5 via `prestigeLevel % 5`):
  | Tier | Color |
  |---|---|
  | 0 | Terre cuite |
  | 1 | Bronze |
  | 2 | Cuivre |
  | 3 | Argent |
  | 4 | Or |
  → Restarts at terre cuite after or (infinite V1 cycle)
- **Runtime parameter**: `pipeline.jar.monthly_subscription_price_cents` to add in `ratis_settings.json` (price not yet final, default value to be validated by PO).
- **Dynamic footer** (seen on `Accueil-haut.png`): "Plus que **X€** → palier suivant" where `X = monthly_subscription_price_cents - current_period_savings_cents` formatted as EUR.

**The `RoiV5_Jar` component from the handoff** (`Ratis_handoff/lib/roi-variants.jsx` lines 210-413) shows 5 different shapes (empty jar, filled jar, pink pig, golden pig, crowned king pig) based on `totalEur`. **This is a visual Claude Design exploration, to be ignored for V1.** Keep the product spec above (1 jar shape, 5 cyclic colors).

**Out of scope V2**:
- Pig phase (piggy bank, prestige 11+)
- Gems phase (Émeraude/Saphir/Rubis/Cristal/Diamant, prestige 16+)
- Jar-smash animation at prestige (shape transition)

→ The exhaustive list of components per screen is in the **§ Iso components (checklist)** below, populated from the Explore SA STAGE 2 return (code audit post-mapping validation).

---

## Iso components (checklist — populated Explore SA STAGE 2)

### Dashboard — `app/(tabs)/index.tsx` (matches `Accueil-haut.png` + `Accueil-bas.png`)
**JSX source**: `Ratis_handoff/lib/ratis-real-v4.jsx` lines 1078-1175

- [ ] **AppHeader sticky** (zIndex 5, `#162028` bg, height ~60px)
  - Greeting "Bonjour" + contextual line (ex: "Belle matinée pour économiser")
  - Season label "SAISON · NIV. 12"
  - Progress bar yellow (season XP)
  - CAB balance "🟡 12 480"
  - 3 icon buttons: 🎁 Shop, 🏆 Achievements (+badge "21"), 📅 Calendar (+badge "5")
- [ ] **Hero row** (flex 1.4 : 1, gap 10):
  - **Left col flex 1.4**: `JarPrestige` (1 SVG jar, 5 cyclic colors by `prestigeLevel % 5`, animated fill Reanimated, ambient sparkles, continuous coin fall)
    - Title "TIRELIRE" pink/coral
    - Big total "47,95€"
    - SVG bocal jar with colorized tier fill
    - "62%" overlay
    - Footer text "Plus que 52€ → palier suivant"
  - **Right col flex 1.0** (column gap 10):
    - `MysteryProductCard` (violet bg, "?" icon, "MYSTÈRE Produit du jour", "+50 cab" gold pill)
    - `JackStreakButton` (coral bg, "STREAK JACK Nourrir Jack" + "+35%" + big "7 JOURS")
- [ ] **`NextAchievementCard`** (compact, full-width, "PROCHAIN SUCCÈS · CUIVRE — Demi-bil 47/50" with progress bar)
- [ ] **`BattlepassCard`** (full-width, cyan gradient `linear-gradient(180deg, #0E7490, #0E5366, #082C3A)` + bg image `lib/ratis-spring-scene.png` opacity 0.22 mixBlendMode: luminosity)
  - "PASS PRINTEMPS 26" + "23j restants" pill
  - "Niv. 12 / 50 SAISON 04"
  - XP bar cyan gradient "340 / 500 XP encore 160 pour Niv. 13"
  - "PROCHAINE RÉCOMPENSE Skin doré Niv. 13" gold banner
  - 5 tier tiles with done/current/locked icons
- [ ] **`MissionsBlock`** (wrapper with `lib/cabecoin-chest.svg` SVG overlay opacity 0.32 scaleX(-1) between the 2 cards)
  - Weekly card (violet border) "★ MISSIONS DE LA SEMAINE 2/4" + 4 mission rows
  - Daily card (orange border) "📅 MISSIONS DU JOUR 1/4" + 4 mission rows
  - Each row: checkbox + label (strikethrough if done) + GameButton "+XP"
- [ ] **`EnrichissementCard`** (gold gradient + 💡 + "Compléter" + sub "Yaourt grec Andros — la marque" + full-width gold CTA "+0,25€")

### Liste — `app/(tabs)/liste.tsx` (matches `Liste Courses.png` + `Liste itineraire.png`)
**JSX source**: `Ratis_handoff/lib/ratis-liste.jsx` + `ratis-liste-ui.jsx`

- [ ] **`PageTitle`** "Ma liste" + 2 icon buttons (map, more)
- [ ] **`SegmentedTabs`** ("Liste · 6" vs "Itinéraire", terracotta accent)
- [ ] **Liste tab**:
  - **`AddBar`** input "Ajouter un produit..." + 3 emoji buttons (suggestions, magic, voice) + "+" submit
  - "Optimiser l'itinéraire" full-width orange button + Scan icon button right
  - **Total card** dark gradient "TOTAL ESTIMÉ 16,17€ 1 coché · 1,85€" | "ÉCONOMIES -4,35€ après optimisation"
  - **`ItemRowLU`** grid: checkbox + category icon + name + brand uppercase + qty stepper +/- + price
  - **CheckBurst animation** (8 particles radial 0.55s cubic-bezier(0.2,0.7,0.4,1) on check)
- [ ] **Itinéraire tab**:
  - Empty state if not optimized
  - Hero summary "Total / Économisé / Trajet 4.4 km 42min"
  - `RouteStopCard` × N (store name, distance, time, items count, savings badge)
  - "Démarrer l'itinéraire" button

### Produit — `app/(tabs)/produit.tsx` (matches `Produit.png`)
**JSX source**: `Ratis_handoff/lib/ratis-other-tabs.jsx` lines 360-484

- [x] **`PageTitle`** "← Fiche produit" + heart + share icons
- [x] **Hero card** (emoji 80×80 + "NESPRESSO Capsules Café Vivalto Lungo x10" + EAN "7640110350683")
- [x] **Consensus price card** (jar-pink bg, bocal SVG icon, "MEILLEUR PRIX 4,20€ 7 magasins · 4 km autour")
- [x] **`SegmentedTabs`** (Prix · 7 / Infos)
- [x] **Prix tab**: `PriceRow` × N
  - Best store gold tile "Auchan Nation 2.8 km — 4,20€ MEILLEUR" + 👑 emoji
  - Other stores "+X%" red text
- [x] **Infos tab**: characteristics table (Quantité, Marque, Origine, Poids, Conservation)
- [x] **"+ Ajouter à ma liste"** full-width coral CTA bottom

### Profil — `app/(tabs)/profil.tsx` (matches `Profil.png`)
**JSX source**: `Ratis_handoff/lib/ratis-other-tabs.jsx` lines 562-627

- [ ] **`PageTitle`** "Profil" + ⚙ settings button right
- [ ] **Avatar section** (gradient circle + emoji 🐀 + "Marie L." name + "@marie.l" + "★ Niv. 12" gold badge)
- [ ] **Stats grid** 3 tiles (12 480 CAB gold + 47 SCANS purple + 48€ ÉCONOMIES green)
- [ ] **`MenuGroup` "Récompenses"** (purple border):
  - 🎁 Boutique — Cartes cadeaux · bonus
  - 🏆 Succès — 7 / 24 débloqués
  - 👥 Parrainage — Invite un ami · +500 cab
- [ ] **`MenuGroup` "Compte"**:
  - Mes informations
  - Notifications
  - (Langue, Confidentialité — may be out of V1)
- [ ] **`MenuGroup` "Session"** (danger color):
  - Se déconnecter

### Mission popup modal (matches `Mission popup.png`)
**JSX source**: `Ratis_handoff/lib/ratis-real-v4.jsx` lines 805-900 (`MissionsModal`)

- [ ] **Bottom sheet** (radius 24px top, gradient `linear-gradient(180deg, #1c2730, #15191c)`)
- [ ] **Backdrop overlay** `rgba(0,0,0,0.65)` zIndex 200
- [ ] **Animations**: fadeIn 0.2s ease-out (overlay) + slideUp 0.26s cubic-bezier(0.2,0.9,0.3,1.2) (sheet)
- [ ] **Drag handle visual** + close button
- [ ] **Header** "Tes missions / Missions actives"
- [ ] **2 MissionsCard** (weekly + daily, same as Dashboard)

### Achievements modal "Succès" (matches `Succès.png`)
**JSX source**: `Ratis_handoff/lib/ratis-achievements-ui.jsx` lines 226-336 (`AchievementsModal`)

- [ ] **Full-screen modal** (linear-gradient dark bg, zIndex 20)
- [ ] **Header** "Succès · Collection" + close button
- [ ] **Stats bar** 3 pills (Débloqués X/24, En cours, Score %)
- [ ] **Status filter tabs** (Tous / Débloqués / En cours / À faire)
- [ ] **Category chip filter row** (horizontal scroll)
- [ ] **Grid 3 columns** of `AchievementCard` (aspect 3/4, metallic frame, rarity-based)
  - Holographic shine animation `achHoloShine` 4.5s linear gradient sweep (unlocked rare+)
  - Burst rays rotation `achBurstSpin` 8s linear (unlocked legendary)
- [ ] **`AchievementDetailModal`** (281 width center overlay, on card click)
- [ ] **`AchievementUnlockToast`** (slide-in + pop icon 0.6s cubic-bezier bounce)

### Tab bar bottom — global (matches tab bar in screenshots)
**JSX source**: `Ratis_handoff/lib/ratis-real-v4.jsx` lines 731-779 (`RatisTabBar`)

- [ ] Bg `rgba(22,32,40,0.95)` + `backdrop-filter: blur(12px)` (`expo-blur` BlurView)
- [ ] Border-top `1px solid rgba(255,255,255,0.06)` zIndex 20
- [ ] 5 tabs ordered: index | liste | scan (FAB centred -20px top) | produit | profil
- [ ] **Scan FAB**: 60×60px, border 2.5px terracotta, bg `rgba(22,32,40,0.98)`, shadow `0 5px 0 rgba(100,40,20,0.6), 0 10px 20px rgba(218,119,86,0.3), inset 0 1px 0 rgba(255,255,255,0.08)`
- [ ] Active tab: indicator dot 4×4 terracotta top + icon/label colored terracotta, inactive `rgba(255,255,255,0.45)`
- [ ] Icon SVG paths: home (fill mode), liste (lines stroke), camera FAB, shopping bag (stroke), user (stroke)

---

## Tokens (sync with `constants/theme.ts`)

### Explore SA STAGE 2 audit — `theme.ts` ✅ covers 100% of V5 tokens

The STAGE 2 audit confirmed that `ratis_client/constants/theme.ts` (from PR2 #281 + PR4.1 adjustments) already covers **all colors, spacing, radii, shadows, typography** used in the 6 canonical V5 JSX files. No missing critical tokens.

### Main colors (reminder for SA Dev)
- Background: `#1a2428` (base), `#1c2730` (scroll root), `#162028` (header), `#27293A` (cards std)
- Primary: `#DA7756` (terracotta), `#FFB800` (gold), `#FF6B9D` (jar pink), `#A78BFA` (violet weekly), `#FF6B35` (orange daily)
- Secondary: `#4DD4B3` (teal), `#67E8F9` (cyan), `#EF4444` (coral), `#FFE066` (gold hi)
- Text: `#FFF` primary, `rgba(255,255,255,0.45)` secondary, `rgba(255,255,255,0.30)` tertiary

### Key gradients
- BattlepassCard: `linear-gradient(180deg, #0E7490 0%, #0E5366 65%, #082C3A 100%)`
- Mystery card: `linear-gradient(160deg, #2D2438, #1F1A2E)` (purple)
- Jar pink card: `linear-gradient(160deg, #2A1A1A, #1F1212)` (mystery jack red)

### Shadows pattern (Clash Royale 3D)
```
boxShadow: [
  { offsetY: 5, color: 'rgba(0,0,0,0.X)', blur: 0 },        // hard drop
  { offsetY: 12, color: 'rgba(0,0,0,0.4)', blur: 22 },      // diffuse
  { inset: true, offsetY: 1-2, color: 'rgba(255,255,255,0.08-0.18)' },  // top highlight
]
```

### Typography
- Font family Inter (weights 400-900)
- Sizes seen: 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 24, 26, 28, 32, 44 px
- Negative letter-spacing on headings: -0.1 to -1.28 px
- Positive letter-spacing on uppercase labels: 0.5 to 1.4 px

### Token-sync process (already OK for SA Dev Front)
- No token additions needed in PR strict iso V1
- SA Dev must use EXCLUSIVELY `Colors.*`, `Spacing.*`, `Radii.*`, `Shadows.*`, `Typography.*` from `theme.ts` — any hardcoded `#xxx` without justification will be rejected

---

## Iso process (mandatory for SA Dev)

1. **Side-by-side mode**: SA Dev opens 2 panels — `ratis-real-v4.jsx` on the left, `<component>.tsx` on the right
2. **Manual visual diff**: for each detail (colors, spacing, radius, shadow, font, weight, animations) — compare and match
3. **Reanimated values matching**: duration, easing, delay of framer-motion → equivalent documented Reanimated values (cf mapping table above)
4. **Storybook story per component**: reproducible visual oracle. Each handoff variant = 1 story.
5. **PR description**: documents EVERY divergence with justification. No vague "minor visual adjust".

---

## Formal anti-patterns

- ❌ "I found a cleaner / more minimal / more elegant design" → **no**, we follow the handoff
- ❌ "I simplified the animation because it was hard in RN" → **no without solid technical justification** (Reanimated 4 can do ~99% of framer-motion)
- ❌ "I invented a variant" → **no**
- ❌ Missing PNG asset replaced by a functional placeholder → **no**, we port the asset (require + expo bundle)
- ❌ Color "approximated" without looking up the exact hex code in the JSX → **no**
- ❌ Spacing `p-3` rendered as `padding: 16` instead of `padding: 12` → **no** (Tailwind `3` = 12px)
- ❌ `# eslint-disable` or `// @ts-ignore` to hide a typing issue related to a port → **no**

---

## PR4.1 delivered vs V5 expected divergences (TOP 10 — STAGE 2 audit)

| # | Element | V5 expected (screenshot/JSX) | PR4.1 currently delivered | Effort |
|---|---|---|---|---|
| 1 | **Hero jar layout** | Single SVG jar with 5 cyclic colors (`prestigeLevel % 5`), animated fill, staggered sparkles, falling coins, conditional glow | `JarPrestige` Skia partial — concept correct (1 shape, 5 colors) but visual details to align | MEDIUM |
| 2 | **Hero row layout** | flex 1.4 (jar) : 1.0 (mystery+jack stacked column) | PR4.1 layout differs (ratios to verify) | MEDIUM |
| 3 | **MysteryProductCard styling** | Violet `#3D2E5A` bg + corner glow + "?" + "MYSTÈRE Produit du jour" + "+50 cab" gold pill | Likely OK but gradient to fine-tune | SMALL |
| 4 | **JackStreakButton** | Coral bg + rat emoji + "STREAK JACK Nourrir Jack" + "+35%" + big "7 JOURS" | To verify vs PR4.1 JackCard | SMALL |
| 5 | **BattlepassCard asset** | bg image `Ratis_handoff/lib/ratis-spring-scene.png` opacity 0.22 mixBlendMode luminosity | PR4.1 used corner glow shimmer cyan instead (asset not ported) — **resolve**: port the PNG | MEDIUM |
| 6 | **Missions chest overlay** | `lib/cabecoin-chest.svg` overlay z-2 between weekly+daily cards (opacity 0.32, scaleX(-1) mirror), opaque strip masking gap | PR4.1 = MissionsList without chest visual bridge | MEDIUM |
| 7 | **Animations** | CheckBurst 0.55s, jar coins fall 3.2-4.1s loop, sparkle staggered 5.2-7.0s, jar halo pulse 2.4s (full state), jar ray spin 14s (full state) | PR4.1 partial (jar particles exist), to complete | MEDIUM |
| 8 | **Buttons 3D shadows** | Hard drop `0 4-5px 0` + diffuse `0 12px 22px 0.4` + insetTop `inset 0 1-2px rgba(255,255,255,0.08-0.18)` | PR4.1 likely flat OR partial 3D — to verify consistency | MEDIUM |
| 9 | **Tab bar FAB** | 60×60 centred -20px top, terracotta border 2.5px, custom shadow combo | PR4.1 inherits Expo Router default tab bar — **to override custom** | MEDIUM |
| 10 | **Dynamic footer** | "Plus que **52€** → palier suivant" on jar (recalculated from savings) | Probably static or absent | SMALL |

### Required asset porting (`Ratis_handoff/lib/`)
- [ ] `ratis-spring-scene.png` → `ratis_client/assets/images/spring-scene.png` (BattlepassCard bg)
- [ ] `cabecoin-chest.svg` → `ratis_client/assets/images/cabecoin-chest.svg` or inline `react-native-svg` (MissionsBlock overlay)
- [ ] `jack-mascot.svg` → verify if already in place or convert
- [ ] `market.svg` → ListeUI bg subtle (opacity 0.15)

### Missing design-system components (to create in parallel)
- [ ] `components/design-system/modal.tsx` (reusable Sheet/BottomSheet)
- [ ] `components/design-system/toast.tsx` (bottom-positioned Toast)
- [ ] `components/design-system/avatar.tsx` (gradient circle Avatar)
- [ ] `components/design-system/stepper.tsx` (Qty +/- ItemRow)
- [ ] `components/design-system/check-burst.tsx` (particle animation on Liste check)

---

## Runtime settings to add

```json
{
  "pipeline": {
    "jar": {
      "monthly_subscription_price_cents": 999,
      "_doc": "Prix de l'abonnement Ratis mensuel en centimes. Définit le seuil 100% de la jauge (= prestige). À ajuster au fil de la vie produit."
    }
  }
}
```

(Value 999 = 9.99€ by default, to be validated by PO. The jar UI consumes this setting via `useEnrichissement()` — backend already in place in principle.)

## V1 out of scope

- **Pig phase** (piggy bank, prestige 11+ with smash animation) → V2
- **Gems phase** (Émeraude/Saphir/Rubis/Cristal/Diamant, prestige 16+) → V2
- **Jar-smash animation** at prestige (shape transition) → V2
- **Complex multi-stagger layoutAnim animations** → V2 if deemed critical
- **Dark mode** → V2
- **Dynamic content adaptation** (tablets, foldables) → V2
- **Automated Storybook screenshot diff** CI (Chromatic) → V1.5 post-impl
- **`scan-history.tsx`, `(auth)/login.tsx`, `my-info.tsx`, `referral.tsx`** → remain V0 (not in V5 screenshots)

---

## V4 tests removed (R33 bypass user-acted 2026-05-10)

> Code health audit PR #364, item F-6 — user decision 2026-05-10. Removal of **24 stub test files**, with no real test logic, in `ratis_client/__tests__/components/`.

**Context**: during the V4 → V5 refactor (visual-iso-v5 chunks), the V4 components were wholesale wiped. To keep Jest green during the transition, each V4 test was replaced with a `describe.skip(...)` + `it('placeholder', () => {})` stub (14-17 lines per file). The comment at the top mentioned "Re-enable / restore in the chunk that rebuilds the corresponding component" and pointed to commit `01d62ff` (original content in git history).

**Pre-removal audit (24 files, all confirmed dead)**:
1. **No V4 target component exists yet** in `ratis_client/components/` at the paths expected by the tests. Exhaustive verification via `find` cross-tree.
2. **One exception**: `ui/app-header.tsx` no longer exists at that path but `components/dashboard/app-header.tsx` was rebuilt V5 (chunk 3). It has a completely different anatomy/props. The V4 stub only pointed to code that no longer existed at that path → dead.
3. **All files** are minimal stubs (1 `it('placeholder')` each) — zero test logic to port.
4. **Original test contents** remain accessible via git history (commit `01d62ff` mentioned in each stub header) in case a future SA Dev wants to draw inspiration for rebuilding V5 coverage.

**R33 bypass justification (Never delete a test)**:

R33 says "Never delete a test". The user explicitly bypassed this rule on 2026-05-10 on the grounds that:
- A `describe.skip(...) { it('placeholder') {} }` stub is NOT a test — it is a pointer to a test that was removed from the active history.
- The original V4 content no longer tested anything relevant: props/structure/anatomy are completely different in V5.
- Keeping 24 empty stubs clutters Jest output + maintenance overhead for zero safety net value.
- If V5 coverage is needed: write new TDD tests against V5 components, not restore obsolete V4 ones.

**Reference**: code health audit post-merge PR #364 (achievements V1), item F-6.

**Remaining coverage**: `ratis_client/__tests__/` retains all other tests (client services, React Query hooks, screens, integration). Only the 24 visual-iso-v5 stubs were removed.

> ⚠️ **V5 re-coverage**: when a new test is written for a V5 component (e.g.: `JarPrestige`, `MysteryProductCard`, `BattlepassCard`), it will be **net new TDD** — not a restoration of a V4 stub. No re-import from git history.

---

## Coexistence with `ARCH_design_system.md`

| This ARCH (`frontend_strict_iso`) | `ARCH_design_system.md` |
|---|---|
| Handoff fidelity rules | Token + primitives catalogue |
| Iso process (review, Storybook) | Primitives code conventions |
| Open divergences table | Published primitives list |
| framer-motion ↔ Reanimated mapping | animations.ts documentation (durations) |

→ The 2 ARCHs are complementary. `frontend_strict_iso` = the rule, `design_system` = the material to follow it.

---

## Implementation workflow

```
[1] Explore SA audit ratis-real-v4.jsx → exhaustive mapping (DONE: voir Q1-Q8 retour)
[2] Orchestrator completes this ARCH (Components + Tokens + Divergences sections)
[3] PO validates final ARCH → commit
[4] Dispatch SA Dev Front: strict iso impl (dedicated worktree)
    - Branch: feat/frontend-strict-iso
    - Approach: progressive patch screen by screen (given the cost of a rebuild from scratch)
    - Tests: Storybook stories + jest snapshots
[5] SA Dev Front delivers → pushes branch
[6] Dispatch SA Reviewer Front: iso code audit (read-only, handoff vs code comparison)
    - Output: markdown report with list of divergences found
[7] PO reviews EAS preview APK post-PR → manual visual report of glitches
[8] SA Dev Front fixes divergences listed (Reviewer + PO report)
[9] PR merged + EAS rebuild if native deps modified
```

---

## Glossary

- **stricto sensu**: exactly, without free interpretation, within the bounds of RN technical constraints
- **handoff**: Claude Design bundle provided by PO on 2026-05-03 (`.design-handoff-2026-05-03/`)
- **iso**: isomorphic, same visual rendering
- **JSX source**: `ratis-real-v4.jsx` — the sole visual source of truth
- **divergence**: any discrepancy between the RN rendering and the expected rendering from the JSX source
- **review iso**: code audit by SA Reviewer to verify that each component matches the handoff
