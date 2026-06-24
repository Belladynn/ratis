---
type: cross-cutting
service: ratis_client
parent: ARCH_CLIENT
related: []
status: in-progress
tags: [expo, strategy, migration, mobile, eas]
updated: 2026-04-24
---

# ARCH — Expo Strategy: migration signals

> Mobile stack strategy: Expo managed + EAS Build + expo-router for V1. No custom native lib. Monitoring of red signals that would justify an ejection (native camera APIs, out-of-date modules, blocking perf issues).
> @tags: expo strategy migration mobile eas eas-build expo-router managed-workflow eject signals
> @status: EN-COURS
> @subs: auto

> Parent : [[ARCH_CLIENT]]

## V1 Decision

**Chosen stack: Expo managed + EAS Build + expo-router.**

Rationale: ecosystem aligned with Ratis requirements (camera, secure-store, location, push, Apple Sign-In), OTA via EAS Update, fast solo-dev shipping, reversible at 10-15% of code if needed. No custom native lib planned for V1 (server-side OCR).

## Why monitor this

Expo is not an architectural prison — 85-90% of the code is standard React Native, reusable elsewhere. But staying on Expo is only justified as long as the benefits (EAS Build, OTA, maintained modules) outweigh the friction (cost, minor lock-in, speed of adopting new iOS/Android features).

**Rule:** we stay on Expo by default. We start preparing a migration only if several red signals converge.

---

## Metrics to watch

### Build & CI

| Metric | Healthy (green) | Watch (yellow) | Alert (red) |
|---|---|---|---|
| EAS iOS build duration | < 15 min | 15-30 min | > 30 min regularly |
| EAS queue wait time | < 5 min | 5-15 min | > 15 min regularly |
| Builds/month (iOS + Android) | < 30 (free tier OK) | 30-80 (Production plan ~$29/month OK) | > 80 (>$150/month) |
| Monthly EAS cost | < 50 € | 50-200 € | > 200 € |

**Action if red:** consider EAS Enterprise ($99/month) OR migration to self-hosted (Mac mini + Fastlane + GitHub Actions).

### Dependencies & native code

| Metric | Healthy | Watch | Alert |
|---|---|---|---|
| Number of custom Expo modules written in Swift/Kotlin | 0-2 | 3-5 | > 5 |
| Bare RN dependencies requiring a custom config plugin | 0-1 | 2-3 | > 3 |
| Abandoned Expo libraries we depend on | 0 | 1 | ≥ 2 |

**Action if red:** evaluate whether the Expo benefit still justifies the cost. The more native code you write, the less Expo helps.

### Expo SDK upgrade

Expo releases one major version per year. At each upgrade, track:

| Metric | Healthy | Watch | Alert |
|---|---|---|---|
| Time spent on upgrade | < 2 days | 3-7 days | > 1 week |
| Breaking changes impacting Ratis | 0-3 minor | 4-10 minor, 1-2 major | > 2 major or perf regression |
| Expo libs we have to replace with bare RN ones | 0 | 1 | ≥ 2 |

**Action if red two consecutive years:** migration to be planned within 6-12 months. The signal indicates we are using Expo outside its comfort zone.

### Features & adoption

| Metric | Healthy | Watch | Alert |
|---|---|---|---|
| Delay "Apple/Google releases feature" → "Expo supports it" | < 3 months | 3-6 months | > 6 months |
| Open Expo issues blocking us in prod | 0 | 1-2 < 30 days | ≥ 1 > 90 days |
| Hacks to work around Expo (monkey-patches, plugin forks, etc.) | 0 | 1-2 | ≥ 3 |

**Action if red:** strong signal of strategic misalignment. Start evaluating a migration.

### App Store policy

| Signal | Action |
|---|---|
| Apple restricts OTA Updates (guideline 3.3.1 or similar actively enforced) | Prepare a strategy without OTA — reduces the benefit of Expo Update |
| The app is rejected for a native reason not documented by Expo | Investigate whether migrating to dev client resolves it |
| Apple deprecates an API that Expo doesn't support yet | Start counting the days — > 90 days = red |

---

## Cumulative decision thresholds

Rough rule — **multiple reds must occur simultaneously to trigger:**

### 🟢 Stay on Expo
- No reds, or 1 isolated red
- Example: EAS cost climbs but stays under 200 €/month

### 🟡 Actively monitor
- 2 reds or 3 yellows
- Example: painful SDK upgrade + >5 custom native modules
- **Action:** audit Expo usage, estimate migration cost, do not migrate yet

### 🔴 Plan the migration
- 3 reds or one "existential" red (blocking feature impossible to work around)
- Example: custom on-device ML requirement + exploding EAS costs + Expo upgrade breaks perf
- **Action:** bare RN migration spec + 2-4 week timeline, execution next quarter

---

## Migration path (for reference)

If the day comes when we decide to leave Expo:

1. **Prebuild.** `npx expo prebuild` generates the native `ios/` and `android/` folders. The React code stays identical.
2. **Replace Expo APIs with bare RN equivalents.** Typically:
   - `expo-camera` → `react-native-vision-camera`
   - `expo-secure-store` → `react-native-keychain`
   - `expo-notifications` → `@react-native-firebase/messaging` + `@notifee/react-native`
   - `expo-router` → `@react-navigation/native` + manual config
3. **Replace EAS Build with Fastlane + GitHub Actions** (or self-hosted Mac mini runner + custom script).
4. **Replace EAS Update** — lose OTA, OR migrate to CodePush (Microsoft, acquired, unclear status) OR write a custom OTA solution (non-trivial).

Overall estimate for Ratis at its V1/V2 state: **1-2 weeks of focused human dev time**, after which the app is 100% independent from Expo.

---

## Periodic review

**At each major Expo SDK release (1×/year):** re-read this doc, record the observed metrics, decide whether we stay 🟢 🟡 🔴.

**At each trigger event:** if we check off a new red, note the date and reason below.

### Log of observed alerts

_(empty to date — V1 starts on Expo 54)_

| Date | Metric | Value | Level | Action taken |
|---|---|---|---|---|
| — | — | — | — | — |
