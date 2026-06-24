---
type: sub-arch
service: ratis_client
parent: ARCH_CLIENT
related: [ARCH_AUTH, ARCH_referral]
status: in-progress
tags: [profil, account, client, menu, stats]
updated: 2026-04-24
---

# ratis_client — ARCH Profile Screen

> Profile screen: avatar/name/handle header, stats (cumulative savings, scans, CAB), menu (referral, scan-history, shop, my-info, logout, DELETE /account). Theme v2 PR #52.
> @tags: profil account client menu stats avatar referral scan-history my-info logout delete-account theme-v2
> @status: EN-COURS
> @subs: auto

> Parent: [[ARCH_CLIENT]] · Relations: [[ARCH_AUTH]], [[ARCH_referral]]

> Status: in progress
> Branch: `feature/profil`
> Updated 2026-04-20 — theme v2 migration (PR #52).

---

## Implementation Checklist

**Base checklist:**
- [ ] Local types defined (inline in each component, no shared file)
- [ ] `profil-header.tsx` (avatar + name + handle centered, no PaperPoster) — TDD before code
- [x] `profil-stats-grid.tsx` — TDD before code (3 stat-boxes gold/coral/orange)
- [x] `profil-menu-group.tsx` — TDD before code (`ScreenCard` wrapper + rows)
- [x] `profil-menu-row.tsx` — TDD before code (colored icon + title + subtitle + chevron)
- [x] `profil.tsx` — assembly + mock data (2 menus: Rewards + Account)
- [x] Smoke tests `profil.tsx`
- [ ] ESLint / TypeScript clean
- [ ] CI pipeline green

**Custom checklist:**
- [ ] Ratis avatar (image/illustration) centered, name + handle + level below
- [ ] 3 stat-boxes (CAB gold / Scans coral / Savings orange)
- [ ] 2 menu-groups (`Récompenses`: Shop/Achievements/Referral — `Compte`: My info/Notifications/Logout)
- [ ] Each `ProfilMenuRow` is `Pressable` (no-op in V1)
- [ ] All strings in a `STRINGS` object per file (i18n preparation)

> ⚠️ One item at a time. Do not move to the next without finishing the current one.

---

## Index

- [Context](#context) [L.50 - L.65]
- [Local types](#local-types) [L.67 - L.105]
- [Mock data](#mock-data) [L.107 - L.140]
- [Components](#components) [L.142 - L.280]
- [Screen profil.tsx](#screen-profiltsx) [L.282 - L.310]
- [Rules](#rules) [L.312 - L.325]
- [Out of scope](#out-of-scope) [L.327 - L.335]

---

## Context

Read before starting:
- `CLAUDE.md`
- `KNOWN_PROBLEMS_INDEX.md`
- `DECISIONS_ACTED.md`
- `ratis_client/ARCH_product.md` — same stack, same theme v2 patterns (ScreenBackground, AppHeader, PageTitleBand, ScreenCard, Design.colors)
- `ratis_client/ARCH_design_system.md` if it exists

Required dependencies (must already exist):
- `@/components/ui/screen-background` — `ScreenBackground` (replaces `BrickWallBackground`)
- `@/components/ui/app-header` — `AppHeader`
- `@/components/ui/page-title-band` — `PageTitleBand`
- `@/components/ui/cards/screen-card` — `ScreenCard` (replaces `PaperPoster`)
- `@/constants/theme` — `Design.colors` (enriched theme v2 palette)
- `expo-linear-gradient` — stats icons (already in package.json)
- `react-native-safe-area-context` — `SafeAreaView`

Reference spec: `docs/superpowers/specs/2026-04-20-screens-theme-v2-design.md` (replaces the old spec `docs/superpowers/specs/_archive/2026-04/2026-04-18-profil-screen-design.md`)

No backend endpoint — everything is static mock in V1.

---

## Local types

Defined at the top of each component, no `types/profil.ts` file (YAGNI — types are simple and not shared).

```ts
// Props MenuRow : icon + accent + title + subtitle? + onPress?
// Props StatsGrid : cabBalance, scanCount, savedCents (tous number)
// Aucun type partagé — les anciens `RewardItem` et `ContributionStats` sont retirés avec
// `ProfilRewards` et `ProfilContributions` (données live = V2).
```

---

## Mock data

All centralized in `profil.tsx`, passed as props to components.

```ts
// ratis_client/app/(tabs)/profil.tsx

const MOCK_USER = {
  firstName:  'Guillaume',
  handle:     'guillaume',
  level:      12,
  avatarSrc:  require('@/assets/images/avatar-ratis.png'),
}

const MOCK_SUMMARY = {
  cabBalance:    1240,   // CAB
  scanCount:     142,    // scans effectués
  savedCents:    3400,   // économies cumulées (34€)
}
```

Menu data (Shop, Achievements, Referral, My info, Notifications, Logout) are static labels inlined in `profil.tsx`. No more `MOCK_REWARDS` or `MOCK_CONTRIBUTIONS` in V1 — live data will come via API in V2.

---

## Components

### `ProfilHeader` (theme v2 refactor)

```tsx
// ratis_client/components/profil/profil-header.tsx

interface ProfilHeaderProps {
  firstName:  string
  handle:     string
  level:      number
  avatarSrc:  ImageSourcePropType
}
```

Centered vertical layout, no PaperPoster:
```
       ┌─────┐
       │ 🐀 │      (Ratis avatar, 96×96, royal violet border)
       └─────┘
       Guillaume
   @guillaume · Niveau 12
```

- Round avatar 96×96, 2px `royalViolet` border
- Name centered `fontWeight='800'`, handle + level in muted below

---

### `ProfilStatsGrid` (new)

```tsx
// ratis_client/components/profil/profil-stats-grid.tsx

interface ProfilStatsGridProps {
  cabBalance:  number   // CAB
  scanCount:   number
  savedCents:  number   // cumulative savings
}
```

3 stat-boxes in a row, each in a `<ScreenCard accent="...">`:
```
┌ 1240 ┐  ┌ 142   ┐  ┌ 34€  ┐
│ CAB  │  │ SCANS │  │ ÉCO  │
└ gold ┘  └ coral ┘  └orange┘
```

- Value `fontSize=22 fontWeight='900'`
- Label `fontSize=10 uppercase` in muted
- Accents: `gold` (CAB) / `coral` (scans) / `orange` (savings)
- `formatCents` for `savedCents`

---

### `ProfilMenuGroup` (new)

```tsx
// ratis_client/components/profil/profil-menu-group.tsx

interface ProfilMenuGroupProps {
  title:    string             // ex: "RÉCOMPENSES" / "COMPTE"
  children: React.ReactNode    // one or more <ProfilMenuRow />
}
```

- Section title (uppercase, muted, spaced) above a `<ScreenCard noPadding>`
- `ProfilMenuRow` children stacked vertically with thin separators between rows
- No card accent by default (neutral glass)

---

### `ProfilMenuRow` (new)

```tsx
// ratis_client/components/profil/profil-menu-row.tsx

interface ProfilMenuRowProps {
  icon:       React.ReactNode     // colored badge (bg + emoji/icon)
  accent:     'teal' | 'coral' | 'gold' | 'violet' | 'orange' | 'red'
  title:      string
  subtitle?:  string
  trailing?:  React.ReactNode     // chevron by default if undefined
  onPress?:   () => void
  destructive?: boolean           // red if true (ex: Logout)
}
```

A pressable row with colored icon badge + text + right-side chevron. Haptic feedback on press.

In V1 `onPress` handlers are no-ops (navigation not implemented). Rows remain `Pressable` for testability.

---

## Screen `profil.tsx`

```tsx
// ratis_client/app/(tabs)/profil.tsx

export default function ProfilScreen() {
  return (
    <View style={styles.root}>
      <ScreenBackground />
      <AppHeader {...headerProps} />
      <PageTitleBand title="Profil" rightIcons={[<SettingsIcon />]} />
      <SafeAreaView style={styles.safeArea} edges={['bottom']}>
        <ScrollView
          style={styles.scroll}
          contentContainerStyle={styles.scrollContent}
          showsVerticalScrollIndicator={false}
        >
          <ProfilHeader {...MOCK_USER} />

          <ProfilStatsGrid {...MOCK_SUMMARY} />

          <ProfilMenuGroup title="RÉCOMPENSES">
            <ProfilMenuRow icon="🎁" accent="gold"   title="Boutique"   onPress={() => {}} />
            <ProfilMenuRow icon="🏆" accent="violet" title="Succès"     onPress={() => {}} />
            <ProfilMenuRow icon="👥" accent="coral"  title="Parrainage" onPress={() => {}} />
          </ProfilMenuGroup>

          <ProfilMenuGroup title="COMPTE">
            <ProfilMenuRow icon="📝" accent="teal"   title="Mes infos"      onPress={() => {}} />
            <ProfilMenuRow icon="🔔" accent="orange" title="Notifications"  onPress={() => {}} />
            <ProfilMenuRow icon="🚪" accent="red"    title="Déconnexion"    destructive onPress={() => {}} />
          </ProfilMenuGroup>
        </ScrollView>
      </SafeAreaView>
    </View>
  )
}
```

The 6 legacy `PaperPoster` sections (header, stats, rewards, contributions, spending, settings) are merged into 2 menu-groups (`Récompenses` + `Compte`). Row target screens are not wired in V1.

---

## Rules

- All visible strings in a `STRINGS` object at the top of the file — never literal strings in JSX
- Amounts in cents in types, `formatCents()` from `@/utils/shopping-totals` for display
- No API calls, no outgoing navigation in V1 — menu rows are no-op but remain `Pressable`
- `Logout` row: accent `red` + `destructive` — no `Alert` or modal in V1
- Components in `components/profil/` — one file per component, one test file per component

---

## Out of scope

- API calls (GET profile, POST logout) — V2
- Navigation to target screens (Shop, Achievements, Referral, My info, Notifications) — V2
- Inline notifications toggle (moved to dedicated `Notifications` screen) — V2
- Profile photo upload / edit — V2
- "Logout" confirmation modal — V2
- Contribution stats (tickets / products / prices) — removed with `ProfilContributions`, to be rethought in V2
- Detailed rewards list (Fnac, Leclerc, Amazon…) — removed with `ProfilRewards`, `Shop` will have its own screen in V2
