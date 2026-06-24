// ratis_client/components/profil/achievements-adapter.ts
//
// Achievements V1 — bridge between the live API shape (`AchievementItem`)
// and the visual primitive shape (`Achievement`) used by the existing
// `AchievementCard` / `AchievementsModal`.
//
// Why an adapter ?
//   - The visual layer (`AchievementCard`) was built against the static
//     mock collection and accepts a tight `Achievement` shape with a
//     `progress / target / status` triple.
//   - The live API returns a richer shape (`progress: null` for V1 — V1.1
//     ships the X/Y bar — plus `unlocked`, `code`, `cab_reward`, etc.).
//   - Rather than refactoring the visual primitive (and risking iso drift
//     with the V5 design baseline), we adapt at the boundary : convert
//     each API item into the legacy shape, render the same component.
//
// `j_y_etais` handling : the legacy type has 7 categories, the API has 8.
// We collapse `j_y_etais` → `seasonal` for the *card visual* (same chip
// colour / icon family) ; the modal still groups by the API bucket
// separately (the user sees a "J'y étais" tab in the live mode).

import type {
  Achievement,
  AchievementStatus,
  CategoryKey,
  RarityKey,
} from '@/components/profil/achievements-data';
import type {
  AchievementCategoryKey,
  AchievementItem,
  AchievementsListResponse,
} from '@/types/achievements';
import { getAchievementIcon } from '@/lib/achievement-icon';

/**
 * Map the API category (8 buckets including `j_y_etais`) to the legacy
 * 7-bucket palette consumed by `AchievementCard`. The "j_y_etais" bucket
 * shares the seasonal palette since it is by construction a closed-window
 * achievement.
 */
function mapCategory(api: AchievementCategoryKey): CategoryKey {
  if (api === 'j_y_etais') return 'seasonal';
  return api;
}

/**
 * Convert one live API item into the visual primitive shape.
 *
 *   - `progress` is V1.1 (returns `null` from the API today). We default to
 *     `0` for locked, `target_value` for unlocked, so the progress bar
 *     reads sensibly until the real signal lands.
 *   - `target` defaults to `target_value ?? 1` (1 for masked secret rows).
 *   - `status` derives from `unlocked` (V1 has no "in_progress" — we'll
 *     re-introduce it once `progress` is wired). Treating "locked with
 *     progress > 0" as in_progress requires the V1.1 `progress` field.
 */
export function toLegacyAchievement(item: AchievementItem): Achievement {
  const target = (item.target_value ?? 1) > 0 ? (item.target_value ?? 1) : 1;
  const progress = item.unlocked ? target : (item.progress ?? 0);
  const status: AchievementStatus = item.unlocked ? 'unlocked' : 'locked';
  // Bug 1 (PO ticket 2026-05-12 wave 3) — the backend ships `icon` as a
  // short code (e.g. "fire", "trophy"), not an emoji. Resolve to the
  // PO-approved emoji via the central helper so the card visual reads
  // correctly (the AchievementCard renders `achievement.icon` as text).
  const icon = getAchievementIcon({ code: item.code, iconCode: item.icon });
  return {
    id: item.id,
    label: item.label,
    description: item.description,
    icon,
    rarity: item.rarity as RarityKey,
    category: mapCategory(item.category),
    progress,
    target,
    status,
  };
}

/**
 * Walk every category bucket in the API response and emit a flat
 * `Achievement[]` in the same order. `null` / `undefined` input → `[]`
 * (defensive — React Query data starts undefined while loading).
 */
export function flattenAchievementsList(
  response: AchievementsListResponse | null | undefined,
): Achievement[] {
  if (!response?.categories) return [];
  const out: Achievement[] = [];
  for (const group of response.categories) {
    for (const item of group.items) {
      out.push(toLegacyAchievement(item));
    }
  }
  return out;
}
