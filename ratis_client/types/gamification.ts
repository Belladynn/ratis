// ratis_client/types/gamification.ts

export interface DailyMission {
  id: string;
  action_type: string;
  difficulty: 'easy' | 'medium' | 'hard';
  target_count: number;
  current_count: number;
  cab_reward: number;
  xp_reward: number;
  /**
   * Backend status for the mission row.
   *
   * - `pending`   : in progress (server-canonical name; was `active` in V0)
   * - `active`    : legacy alias for `pending`, kept for back-compat
   * - `completed` : target reached, claim available
   * - `claimed`   : reward(s) collected
   */
  status: 'active' | 'pending' | 'completed' | 'claimed';
  // ── Buffer + Burst (optional — backend may extend GET /missions later) ────
  /** 'daily' (bufferable) or 'weekly' (not bufferable). */
  frequency?: 'daily' | 'weekly';
  /** Whether this mission can be Buffered at all (catalogue flag). */
  is_boostable?: boolean;
  /** Number of Buffers applied (0..3 daily). */
  buffer_count?: number;
  /** Number of Burst paliers reached (passive trigger). */
  burst_count?: number;
  /** True once the user claimed any Burst palier — locks Buffer permanently. */
  burst_locked?: boolean;
  /** ISO timestamp; deadline extended by Buffer (NULL if never buffered). */
  period_extended_until?: string | null;
  /** Number of Buffer portions already collected (0..buffer_count+1). */
  portions_claimed?: number;
}

export interface MissionsResponse {
  daily: {
    date: string;
    missions: DailyMission[];
  };
  weekly: {
    week_start: string;
    missions: DailyMission[];
  };
}

// ── Buffer / Burst response payloads ─────────────────────────────────────────

/** POST /gamification/missions/{id}/buffer — backend response. */
export interface BufferMissionResponse {
  buffer_count: number;
  target_count: number;
  cab_reward: number;
  /** ISO timestamp; deadline extended by 1 day per Buffer. */
  period_extended_until: string;
}

/** POST /gamification/missions/{id}/claim — backend response (multi-claim). */
export interface ClaimMissionResponse {
  cab_awarded: number;
  portions_claimed_total: number;
  portions_remaining: number;
  /** Mission status after the claim. */
  mission_status: 'pending' | 'completed' | 'claimed';
  /** Optional — server may include the new CAB balance for cheap UI sync. */
  new_cab_balance?: number;
}

/** POST /gamification/missions/{id}/burst-claim — backend response. */
export interface BurstClaimResponse {
  xp_awarded: number;
  burst_count_total: number;
  /** Always true after the first burst-claim (anti-Buffer lock). */
  burst_locked: boolean;
  leaderboard_record_updated?: boolean;
}

// ── Burst leaderboard ────────────────────────────────────────────────────────

export interface BurstLeaderboardEntry {
  user_id: string;
  display_name: string;
  xp_earned: number;
  burst_count: number;
  buffer_count: number;
  mission_action_type: string;
  /** Catalogue qualifier (e.g. "category", "store", "attribute:organic"). */
  mission_qualifier?: string | null;
  /** ISO timestamp of when this record was recorded. */
  recorded_at: string | null;
}

/** GET /gamification/leaderboard/burst-monthly — backend response. */
export interface BurstLeaderboardMonthlyResponse {
  /** YYYY-MM string. */
  month: string;
  top: BurstLeaderboardEntry[];
  your_rank: number | null;
  your_max_xp: number | null;
}

/** GET /gamification/leaderboard/burst-alltime — backend response. */
export interface BurstLeaderboardAlltimeResponse {
  top: BurstLeaderboardEntry[];
  your_rank: number | null;
  your_max_xp: number | null;
}

/**
 * Maps backend `action_type` → human-readable label (FR).
 *
 * Covers every active mission template surfaced by the backend rewards
 * service. The catalogue mixes seven action types ; the FE keeps a
 * coarse-grained mapping (one label per `action_type`) because the
 * `GET /gamification/missions` payload does NOT currently expose the
 * `qualifier` column. Once the backend ships the qualifier alongside the
 * mission row, we can refine labels with the variant (e.g. `attribute:organic`
 * → « Identifie un produit bio »).
 *
 * Wording chosen with the PO (handoff `Ratis_handoff/lib/ratis-liste-ui.jsx`
 * + product brief 2026-05-12). Difficulty / frequency are surfaced separately
 * (target_count + header card) so they stay out of this label.
 */
export const ACTION_LABELS: Record<string, string> = {
  receipt_scan:           'Scanne un ticket de caisse',
  label_scan:             'Scanne une étiquette électronique',
  barcode_scan:           'Scanne un code-barres',
  product_enrich:         'Complète une fiche produit',
  product_identification: 'Identifie un produit',
  scan_distinct:          'Découvre des produits variés',
  promo_found:            'Trouve une promo',
  fill_product_field:     'Complète un champ produit',
  referral:               'Parraine un ami',
};

export function getMissionLabel(action_type: string): string {
  return ACTION_LABELS[action_type] ?? action_type;
}

export interface StreakState {
  streak_days: number;
  multiplier: number;          // 0.0 – 1.0
  food_reserves: number;
  already_fed_today: boolean;
  needs_repair: boolean;
  last_fed_at: string | null;  // ISO date
}

export interface BattlepassState {
  season_name: string;
  current_level: number;
  xp_current: number;
  xp_next_level: number;
  next_reward_label: string;
  next_reward_type: 'cab' | 'xp' | 'skin' | 'multiplier' | null;
}

export interface EnrichissementTask {
  product_ean: string;
  product_name: string;
  missing_field: string;
  cab_reward: number;  // centimes
}
