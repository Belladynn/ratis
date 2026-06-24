// ratis_client/lib/achievement-tier.ts
//
// 5-tier consolidation for the achievements catalogue — PO ticket
// 2026-05-12 (Bug 4 wave 2).
//
// The backend ships a 10-rarity catalogue (terracotta → diamond) per row
// in `seed_achievements.py`. The PO asked for a 5-tier visual band so the
// user reads the difficulty progression at a glance :
//
//   - terre_cuite   : intro / first-touch achievements (target < 5)
//   - bronze        : early consistency (5 ≤ target < 25)
//   - cuivre        : regular user (25 ≤ target < 100)
//   - argent        : power user (100 ≤ target < 500)
//   - or            : legendary / seasonal / secret / target ≥ 500
//
// The mapping is currently MECHANICAL — it consolidates 10 rarities into
// 5 tiers based on the rarity already assigned by the backend. This keeps
// the card visual (`AchievementCard`, which paints frame + ribbon + glow
// from the 10-rarity `RARITIES` table) untouched while giving us a
// stable, low-cardinality grouping for tier-based UI (e.g. tier filters
// in the modal, a "next tier" banner, or a 5-row legend).
//
// Per-achievement override : explicit entries below take precedence over
// the rarity mapping. Use them when the PO challenges a specific row's
// tier (e.g. « v_500 should be 'argent', not 'or' »). The function falls
// back to the rarity-derived tier for any code not in the override table.
//
// This module deliberately does NOT touch the live `RARITIES` constants
// in `components/profil/achievements-data.ts` — the 10-tier visual model
// remains the source of truth for card frame colours ; the 5-tier model
// is an orthogonal grouping used by higher-level UI.

import type { RarityKey } from '@/components/profil/achievements-data';

export type AchievementTier =
  | 'terre_cuite'
  | 'bronze'
  | 'cuivre'
  | 'argent'
  | 'or';

/** Display label (FR) for each consolidated tier. */
export const TIER_LABELS: Readonly<Record<AchievementTier, string>> = {
  terre_cuite: 'Terre cuite',
  bronze: 'Bronze',
  cuivre: 'Cuivre',
  argent: 'Argent',
  or: 'Or',
};

/** Accent hex for each consolidated tier. Aligned with the jar-prestige
 *  palette so the achievement catalogue and the savings jar speak the
 *  same colour language. */
export const TIER_COLORS: Readonly<Record<AchievementTier, string>> = {
  terre_cuite: '#A0552D',
  bronze: '#7F5832',
  cuivre: '#B87333',
  argent: '#C0C0C0',
  or: '#D4AF37',
};

/**
 * Rarity → tier consolidation. The 10-rarity backend model collapses to
 * the 5-tier PO model as follows :
 *
 * | Rarity          | Tier         | Rationale                              |
 * |-----------------|--------------|----------------------------------------|
 * | terracotta      | terre_cuite  | intro tier (1:1 mapping)               |
 * | bronze          | bronze       | early consistency (1:1 mapping)        |
 * | copper          | cuivre       | regular user (1:1 mapping)             |
 * | silver          | argent       | advanced (1:1 mapping)                 |
 * | gold            | or           | legendary tier (1:1 mapping)           |
 * | emerald         | or           | rare gem tier rolls into « or »        |
 * | sapphire        | or           | rare gem tier rolls into « or »        |
 * | ruby            | or           | rare gem tier rolls into « or »        |
 * | crystal         | or           | top-tier rolls into « or »             |
 * | diamond         | or           | top-tier rolls into « or »             |
 */
const RARITY_TO_TIER: Readonly<Record<RarityKey, AchievementTier>> = {
  terracotta: 'terre_cuite',
  bronze: 'bronze',
  copper: 'cuivre',
  silver: 'argent',
  gold: 'or',
  emerald: 'or',
  sapphire: 'or',
  ruby: 'or',
  crystal: 'or',
  diamond: 'or',
};

/**
 * Per-achievement explicit overrides — empty by default. Add an entry
 * here when the PO contests the mechanical mapping for a specific row.
 *
 * Example :
 *   'v_1000': 'argent',  // 1000 scans is power user, not legendary
 */
const ACHIEVEMENT_OVERRIDES: Readonly<Record<string, AchievementTier>> = {};

/**
 * Resolve the 5-tier band for an achievement. Lookup order :
 *   1. Per-achievement override (if `code` is in `ACHIEVEMENT_OVERRIDES`)
 *   2. Rarity-derived tier from `RARITY_TO_TIER`
 *
 * Returns the rarity-derived tier when both lookups fail (shouldn't
 * happen — every rarity is covered).
 */
export function getAchievementTier(input: {
  code: string;
  rarity: RarityKey;
}): AchievementTier {
  const override = ACHIEVEMENT_OVERRIDES[input.code];
  if (override) return override;
  return RARITY_TO_TIER[input.rarity];
}

/** Resolve the display colour for an achievement's consolidated tier. */
export function getAchievementTierColor(input: {
  code: string;
  rarity: RarityKey;
}): string {
  return TIER_COLORS[getAchievementTier(input)];
}

/** Resolve the FR display label for an achievement's consolidated tier. */
export function getAchievementTierLabel(input: {
  code: string;
  rarity: RarityKey;
}): string {
  return TIER_LABELS[getAchievementTier(input)];
}
