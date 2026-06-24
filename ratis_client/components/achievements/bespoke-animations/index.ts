// ratis_client/components/achievements/bespoke-animations/index.ts
//
// Achievements V1 — bespoke animation registry (PR 8/8).
//
// Each Diamant achievement gets its OWN unlock cinematic. The registry maps
// the catalog `code` → React component. Adding a new Diamant in production
// is a 2-step :
//   1. Create the component file under `bespoke-animations/<code>.tsx`
//   2. Register it here.
//
// Backend stays agnostic — it just sets `has_bespoke = true` on the unlock
// payload. The FE looks the code up here to decide whether to render the
// generic <AchievementCelebrationModal /> or the custom component.
//
// V1 ships with 2 polished placeholders ; visual fidelity is iterated in
// V1.1+ (each Diamant unlock should feel hand-crafted by then).

import type React from 'react';

import { YearLongStreakBespoke } from './year-long-streak';
import { KonamiBespoke } from './konami';
import type { BespokeUnlockProps } from './types';

export type { BespokeUnlockProps } from './types';

/**
 * Map of achievement-code → bespoke unlock component. Codes match the
 * catalog seed (`alembic/versions/20260510_1030_seed_achievements_v1.py`).
 *
 * V1 entries :
 *   - `r_365`         — 365j streak (Diamant `streak`)
 *   - `sec_konami`    — Konami code (Diamant `secret`)
 */
export const BESPOKE_ANIMATIONS: Readonly<
  Record<string, React.FC<BespokeUnlockProps>>
> = {
  r_365: YearLongStreakBespoke,
  sec_konami: KonamiBespoke,
};

/**
 * Convenience predicate — returns true iff `code` resolves to a registered
 * bespoke component. Handles `null` / `undefined` to keep the call sites
 * tidy in the notification handler / celebration modal.
 */
export function hasBespoke(code: string | null | undefined): boolean {
  if (!code) return false;
  return code in BESPOKE_ANIMATIONS;
}
