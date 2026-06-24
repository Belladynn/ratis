---
type: sub-arch
service: ratis_client
parent: ARCH_CLIENT
related: [ARCH_REWARDS, ARCH_gamification]
status: in-progress
tags: [feed-jack, streak, gamification, client, mascot]
updated: 2026-04-24
---

# ratis_client — ARCH Feed Jack (frontend)

> `JackWidget` component (gamification mascot) on the home screen: idle/hungry/happy/broken states driven by the daily streak (DA-09 manual catch-up on reconnection). Frontend of the Feed Jack system documented on the `ARCH_gamification.md` side.
> @tags: feed-jack streak gamification client mascot jack idle hungry happy broken design-phase
> @status: EN-COURS
> @subs: auto

> Parent : [[ARCH_CLIENT]] · Relations : [[ARCH_REWARDS]], [[ARCH_gamification]]

> Status: in progress — design phase
> Branch: `feature/feed-jack`
> Backend: `ratis_rewards/ARCH_gamification.md` → Feed Jack section

---

## Implementation Checklist

- [x] Catch-up decision actioned — **manual on reconnection** (DA-09)
- [ ] `JackWidget` component — states idle / hungry / happy / broken
- [ ] Screen or widget integrated in the home (location TBD)
- [ ] `POST /rewards/streak/feed` call on tap
- [ ] Display active multiplier (`+X% on your earnings`)
- [ ] Food reserve indicator
- [ ] Reserve purchase flow (`POST /rewards/streak/purchase-reserve`)
- [ ] Catch-up screen `StreakRepairModal` (shown if `needs_repair: true` on reconnection)
- [ ] Jack animations (idle loop, feed, break)
- [ ] Snapshot tests + API mock integration tests
- [ ] i18n: all strings via `t('key')`

> ⚠️ One item at a time.

---

## Index

- [Context](#context)
- [Jack — visual identity](#jack--visual-identity)
- [Jack's states](#jacks-states)
- [Components](#components)
- [User flows](#user-flows)
- [API calls](#api-calls)
- [Push notifications](#push-notifications)
- [Displayed parameters](#displayed-parameters)
- [Out of scope](#out-of-scope)

---

## Context

Feed Jack is Ratis's **daily streak**. Jack is a character (mascot) that the user feeds every day by performing an action in the app. The streak increases the CAB and XP earnings multiplier (+5%/day, max +100%).

Jack is not a simple counter — it's an identity. It has an emotional state that reflects the health of the streak. The UX goal: create an attachment that encourages daily connection without being anxiety-inducing.

Dependencies:
- Backend `POST /api/v1/rewards/streak/feed` — see ARCH_gamification backend
- Backend `GET /api/v1/rewards/streak`
- Backend `POST /api/v1/rewards/streak/purchase-reserve`

---

## Jack — visual identity

Jack is a stylized creature (style to be defined with the designer — reference: clean Tamagotchi, Duolingo Owl without the anxiety-inducing side). A few principles:

- **No guilt-tripping**: Jack doesn't "die", he falls asleep. Break = Jack sleeps, not Jack dead
- **Non-intrusive celebration**: short animation on feed, no fanfare that interrupts the user journey
- **Immediate readability**: Jack's state is understandable at a glance without reading text

---

## Jack's states

| State | Condition | Visual | Animation |
|---|---|---|---|
| `idle` | `streak_days > 0`, fed today | Happy Jack, vivid colors | Gentle breathing (loop) |
| `hungry` | `streak_days > 0`, not yet fed today | Slightly hungry Jack | Small waiting movement |
| `sleeping` | `streak_days = 0` (never fed or broken streak) | Sleeping Jack | ZZZ loop |
| `repair` | `needs_repair: true` (gap = 1 day, 0 reserves) | Injured Jack — bandage | Help-call animation |
| `frozen` | Auto-freeze just applied (brief visual feedback) | Jack with ice shield | Brief flash then `hungry` |

---

## Components

### `JackWidget`

Main widget, embeddable in the home or a dedicated screen (location TBD depending on design).

```typescript
interface JackWidgetProps {
  streakDays: number;
  multiplier: number;       // 0.0 → 1.0
  foodReserves: number;
  alreadyFedToday: boolean;
  onFeed: () => Promise<void>;
  onPurchaseReserve: () => void;
}
```

Displays:
- Animated Jack according to his state
- Streak counter (`X days`)
- Active multiplier (`+X% on your earnings`) — hidden if `streakDays = 0`
- Reserve icon with badge count
- Tappable button/area to feed Jack (disabled if `alreadyFedToday`)

### `StreakMultiplierBadge`

Compact reusable badge (displayed on CAB/XP earnings in other screens to recall the bonus).

```typescript
interface StreakMultiplierBadgeProps {
  multiplier: number;   // 0.35 → displays "+35%"
}
```

Hidden if `multiplier = 0`.

### `FoodReserveModal`

Reserve purchase modal. Displays the cost in CABs, current stock, allowed maximum, a quantity selector.

```typescript
interface FoodReserveModalProps {
  currentReserves: number;
  maxReserves: number;
  costPerReserveCab: number;
  userCabBalance: number;
  onPurchase: (quantity: number) => Promise<void>;
  onClose: () => void;
}
```

### `StreakRepairModal` *(Option B — if manual catch-up)*

Shown on first feed if the streak was broken yesterday and the user has reserves.

```typescript
interface StreakRepairModalProps {
  daysLost: number;            // always 1 in V1
  reservesAvailable: number;
  streakBeforeLoss: number;    // displayed: "Your X-day streak can be saved"
  onRepair: () => Promise<void>;   // POST /streak/feed?repair=true
  onDismiss: () => void;           // POST /streak/feed normal
}
```

---

## User flows

### Nominal flow — daily feed

1. User opens the app → `GET /rewards/streak` (on mount)
2. Jack displayed in `hungry` state (not yet fed)
3. User taps on Jack → brief loader
4. `POST /rewards/streak/feed` → response `{streak_days: N, multiplier: 0.Nx, xp_earned: Y}`
5. Feed animation (happy Jack)
6. Streak counter updated, `+X%` badge displayed
7. Button disabled for the rest of the day

### First day flow (streak = 0)

1. Jack in `sleeping` state
2. Tap → `POST /streak/feed`
3. "Jack wakes up" animation
4. Streak = 1, multiplier = 5%

### Reserve purchase flow

1. Tap on reserve icon → `FoodReserveModal` opens
2. Select quantity → display total cost in CABs
3. Confirm → `POST /streak/purchase-reserve`
4. Modal closes, badge count updated

### Auto-freeze flow (reserves available, DA-09)

1. User opens the app after missing N days (N ≤ reserves)
2. `GET /rewards/streak` → `needs_repair: false`, `frozen_days_used: N` (info displayed)
3. Jack displayed in `frozen` state briefly (shield), then `hungry`
4. Subtle toast: "Jack used X reserve(s) to protect your streak 🧊"
5. Tap → normal feed, streak += 1

### Manual repair flow (0 reserves, gap = 1 day, DA-09)

1. User opens the app exactly 1 day after missing (gap = 1 day, 0 reserves)
2. `GET /rewards/streak` → `needs_repair: true`
3. Jack in `repair` state (bandage)
4. `StreakRepairModal` shown automatically
5. Choice **"Repair (X CABs)"** → `POST /streak/repair` → streak restored, Jack `hungry`
6. Choice **"Let it go"** → `POST /streak/feed` → streak restarts at 1, Jack `idle`

### Broken streak flow (gap ≥ 2 days without coverage)

1. `GET /rewards/streak` → `needs_repair: false`, `streak_days: 0`
2. Jack in `sleeping` state — no modal
3. Tap → normal feed, streak restarts at 1, "Jack wakes up" animation

---

## API calls

| Action | Method | Endpoint | Auth |
|---|---|---|---|
| Load state | `GET` | `/api/v1/rewards/streak` | ✅ |
| Feed Jack | `POST` | `/api/v1/rewards/streak/feed` | ✅ |
| Emergency repair (direct CABs) | `POST` | `/api/v1/rewards/streak/repair` | ✅ |
| Buy reserves | `POST` | `/api/v1/rewards/streak/purchase-reserve` | ✅ |

`GET /streak` and `POST /streak/feed` response:
```typescript
// Body POST /streak/feed (timezone optional — sent on first call or if changed)
interface FeedBody {
  timezone?: string;   // IANA string ex: "Europe/Paris" — stored server-side (DA-11)
}

// Response GET /streak and POST /streak/feed
interface StreakState {
  streak_days: number;
  multiplier: number;           // 0.0 – 1.0
  food_reserves: number;
  already_fed_today: boolean;
  needs_repair: boolean;        // true if gap = 1 day AND food_reserves = 0
  frozen_days_used: number;     // number of reserves auto-consumed at this connection (0 if no freeze)
  timezone: string;             // currently stored timezone
}
```

---

## Push notifications

| Event | Type | Message |
|---|---|---|
| Streak not done (D-1 = 22h) | `feed_jack_reminder` | "Jack is hungry! Feed him before midnight to keep your streak." |
| Reserve used for catch-up | `feed_jack_reserve_used` | "Streak saved! You used a food reserve for Jack." |
| Last reserve used | `feed_jack_last_reserve` | "No more reserves! Buy some to protect your next streak." |

> Notifications managed by `ratis_notifier`. New `notification_types` to be added in `ratis_settings.json` during implementation.

---

## Displayed parameters

| Parameter | Source | Display |
|---|---|---|
| `streak_days` | `/streak` | "X days" |
| `multiplier` | derived: `streak_days × 5%` | "+X% on your earnings" |
| `food_reserves` | `/streak` | Numeric badge on reserve icon |
| `food_reserve_cost_cab` | settings via API (to expose) | In `FoodReserveModal` |
| `max_food_reserves` | settings via API | Limit displayed in modal |

---

## Out of scope V1

- Premium animations (particles, shaders) — V2
- Streak history (GitHub-style calendar graph) — V2
- Social streak sharing — V2
- Customizable Jack (purchasable costumes) — V2 or later, depends on `reward_type = 'skin'`
- Community / friends streak — to be studied
