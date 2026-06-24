// ratis_client/lib/achievement-icon.ts
//
// Bug 1 (PO ticket 2026-05-12 wave 3) — per-achievement icon mapping.
//
// The backend (`ratis_core/seed_achievements.py`) stores SHORT CODES in
// the `icon` column (e.g. "x", "fire", "trophy", "coin"). The mobile UI
// rendered those codes verbatim on the achievement cards, which looked
// empty / cryptic to the PO ("v_first" rendered "x" instead of an icon).
//
// This helper translates :
//   1. the achievement `code` (preferred — explicit per-row override)
//   2. the backend `icon` code (fallback — covers any achievement not in
//      the explicit map)
//   3. `❓` ultimate fallback (unknown row — shouldn't happen post-seed)
//
// Source of truth for the per-row icon picks :
//   - `Ratis_handoff/lib/ratis-achievements-data.jsx` lines 38-119 (V1
//     achievement codes — authoritative, PO-approved during the original
//     achievements UI iso)
//   - For codes the handoff doesn't cover (`r_revive`, `sec_3am` etc. are
//     covered ; new ones would go through fallback) we use the backend
//     icon-code → emoji mapping in `ICON_CODE_FALLBACK`.
//
// Why a helper and not patch the static `ACHIEVEMENTS` table ? The live
// API ships `icon: <backend code>` (not the emoji), so the adapter calls
// this helper at the boundary. Centralizing here keeps the icon picks
// reviewable as a single 23-row table and lets the PO challenge each
// assignment without combing through component code.
//
// Mirrors the shape of `lib/achievement-tier.ts` (introduced in PR #427)
// for consistency.

/**
 * Per-achievement explicit icon picks — 23 V1 entries (matches
 * `ratis_core/seed_achievements.py` minus `sea_winter`, window-closed).
 *
 * Picks are sourced from `Ratis_handoff/lib/ratis-achievements-data.jsx`
 * where present (handoff = authoritative). For V1 codes the handoff
 * doesn't define, we propose a thematic emoji (documented in the PR body
 * as the « PO challenge target » table).
 */
const ACHIEVEMENT_ICONS: Readonly<Record<string, string>> = {
  // ── VOLUME (5 entries) ────────────────────────────────────────────
  v_first: '🎬',   // handoff line 38 — clapperboard for "premier scan"
  v_10: '📋',      // handoff line 39 — clipboard for first regular use
  v_50: '📑',      // handoff line 40 — bookmark tab for milestone
  v_500: '📊',     // handoff line 42 — chart for volume tier
  v_1000: '🏆',    // handoff line 43 — trophy for major milestone

  // ── SAVINGS (5 entries) ───────────────────────────────────────────
  s_1: '🪙',       // handoff line 52 — coin for first euro saved
  s_10: '💵',      // handoff line 53 — dollar bill for 10 EUR
  s_50: '💴',      // handoff line 54 — yen bill for 50 EUR
  s_500: '💷',     // handoff line 56 — pound bill for 500 EUR
  s_day_20: '🌟',  // handoff line 59 — star for "big day savings"

  // ── STREAK (5 entries) ────────────────────────────────────────────
  r_3: '🔥',       // handoff line 66 — flame for streak
  r_7: '🔥',       // handoff line 67 — flame for weekly streak
  r_14: '🔥',      // handoff line 68 — flame for 2-week streak
  r_30: '🔥',      // handoff line 69 — flame for monthly streak
  r_365: '🌌',     // handoff line 71 — milky way for legendary streak

  // ── SOCIAL (2 entries) ────────────────────────────────────────────
  soc_invite_1: '🤝',   // handoff line 78 — handshake for first invite
  soc_invite_10: '🌐',  // handoff line 80 — globe for network of 10

  // ── EXPLORATION (3 entries) ───────────────────────────────────────
  exp_brand_5: '🛒',     // handoff line 89 — cart for variety of brands
  exp_cat_15: '📚',      // handoff line 92 — books for "encyclopediste"
  exp_unknown_10: '🚀',  // handoff line 94 — rocket for pioneer

  // ── SEASONAL (1 entry — sea_winter omis car closed) ───────────────
  sea_summer: '☀️',  // handoff line 108 — sun for summer pass

  // ── SECRET (2 entries) ────────────────────────────────────────────
  sec_konami: '❓',  // handoff line 115 — mystery mark for konami easter egg
  sec_3am: '❓',     // handoff line 111 — mystery mark for 3am-opener egg
};

/**
 * Backend icon-code → emoji fallback. Matches the short codes in
 * `seed_achievements.py` (`"x"`, `"fire"`, `"trophy"`, etc.). Used when
 * an achievement code isn't in `ACHIEVEMENT_ICONS` (defensive — covers
 * any future seed entries that ship before the FE map is updated).
 */
const ICON_CODE_FALLBACK: Readonly<Record<string, string>> = {
  // Volume
  x: '🎬',
  list: '📋',
  list2: '📑',
  chart: '📊',
  trophy: '🏆',
  // Savings
  coin: '🪙',
  bill1: '💵',
  bill2: '💴',
  bill3: '💷',
  star: '🌟',
  // Streak
  fire: '🔥',
  milky: '🌌',
  // Social
  hands: '🤝',
  globe: '🌐',
  // Exploration
  cart: '🛒',
  books: '📚',
  rocket: '🚀',
  // Seasonal
  sun: '☀️',
  // Secret
  qmark: '❓',
};

/**
 * Resolve the emoji for an achievement. Lookup order :
 *   1. explicit per-code map (`ACHIEVEMENT_ICONS`)
 *   2. backend icon-code fallback (`ICON_CODE_FALLBACK`)
 *   3. `❓` ultimate fallback
 *
 * @param input.code optional — achievement code (e.g. `v_first`). When
 *                   provided, takes precedence over `iconCode`.
 * @param input.iconCode optional — backend `icon` field value (short
 *                       code, e.g. `"fire"`). Used when the achievement
 *                       code isn't in the explicit map.
 */
export function getAchievementIcon(input: {
  code?: string | null;
  iconCode?: string | null;
}): string {
  if (input.code && ACHIEVEMENT_ICONS[input.code]) {
    return ACHIEVEMENT_ICONS[input.code];
  }
  if (input.iconCode && ICON_CODE_FALLBACK[input.iconCode]) {
    return ICON_CODE_FALLBACK[input.iconCode];
  }
  return '❓';
}

/**
 * Exported for tests — the 23 V1 codes the helper knows about.
 */
export const KNOWN_ACHIEVEMENT_CODES = Object.freeze(
  Object.keys(ACHIEVEMENT_ICONS),
);
