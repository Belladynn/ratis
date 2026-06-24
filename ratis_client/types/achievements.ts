// ratis_client/types/achievements.ts
//
// Achievements V1 — public API response shape.
// Mirrors the backend serializer in
// `webservices/ratis_rewards/services/achievement_serializer.py`.
//
// The backend exposes 3 endpoints under `/api/v1/rewards/achievements*`
// (cf docs/superpowers/specs/2026-05-09-achievements-v1-design.md
// § "Endpoints publics"). The shape returned by `GET /achievements` is :
//
//   {
//     "categories": [
//       { "key": "volume", "label": "Scans", "items": [AchievementItem, …] },
//       …
//     ]
//   }
//
// Per-item display rules are enforced server-side (secret/hidden/limited-time
// /j_y_etais override) — the FE just renders what it gets.

/** Server-known rarity tiers (10 paliers : terre cuite → diamant). */
export type AchievementRarity =
  | 'terracotta'
  | 'bronze'
  | 'copper'
  | 'silver'
  | 'gold'
  | 'emerald'
  | 'sapphire'
  | 'ruby'
  | 'crystal'
  | 'diamond';

/**
 * Catalog category (7 buckets) + the 8th computed display-only category
 * `j_y_etais` returned by the serializer when a user has unlocked a
 * limited-time achievement that is now closed.
 */
export type AchievementCategoryKey =
  | 'volume'
  | 'savings'
  | 'streak'
  | 'social'
  | 'exploration'
  | 'seasonal'
  | 'secret'
  | 'j_y_etais';

/**
 * Single achievement returned by the serializer.
 *
 * Notes on nullables :
 *   - `code`, `cab_reward`, `target_value` are `null` when the row is masked
 *     because the achievement is `is_secret=true` AND the user has not
 *     unlocked it yet.
 *   - `progress` is `null` for V1 (V1.1 follow-up — the "X/Y" bar).
 *   - `unlocked_at` is `null` when `unlocked === false`.
 */
export interface AchievementItem {
  id: string;
  code: string | null;
  label: string;
  description: string;
  icon: string;
  rarity: AchievementRarity;
  category: AchievementCategoryKey;
  cab_reward: number | null;
  target_value: number | null;
  progress: number | null;
  unlocked: boolean;
  unlocked_at: string | null; // ISO-8601 UTC
  window_open: boolean;
}

export interface AchievementCategoryGroup {
  key: AchievementCategoryKey;
  label: string;
  items: AchievementItem[];
}

export interface AchievementsListResponse {
  categories: AchievementCategoryGroup[];
}

/**
 * Notification payload pushed by `notifier_client.send` when an achievement
 * unlocks. Fields are documented in the spec § "Backend → frontend payload".
 *
 * `sound_intensity` is a 0-3 integer. `has_bespoke` true means the FE should
 * look up `BESPOKE_ANIMATIONS[code]` for a custom unlock cinematic instead of
 * the generic celebration modal.
 */
export interface AchievementUnlockedPayload {
  notif_type: 'achievement_unlocked';
  achievement_id: string;
  code: string;
  label: string;
  description: string;
  rarity: AchievementRarity;
  category: AchievementCategoryKey;
  icon: string;
  cab_granted: number;
  show_modal: boolean;
  has_bespoke: boolean;
  sound_intensity: 0 | 1 | 2 | 3;
}

/** Whitelist of secret events the FE can fire to the backend. */
export type SecretEventName = 'konami_code_entered' | 'app_opened_at_3am'; // pragma: allowlist secret

export interface SecretEventResponse {
  ok: boolean;
  unlocked_count: number;
}
