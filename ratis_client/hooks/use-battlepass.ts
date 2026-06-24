// ratis_client/hooks/use-battlepass.ts
//
// Battle pass query — calls `GET /gamification/battlepass` and adapts the
// backend response into the legacy `BattlepassState` shape consumed by
// `BattlepassCard` and `AppHeader`.
//
// Why an adapter ?
//   The backend ships a milestone-based response :
//     {
//       season: { id, name, ends_at },
//       cab_earned_season: number,
//       milestones: [{ id, milestone_number, cab_required, reward_type,
//                      reward_value, subscriber_only, status }, …],
//     }
//   The frontend was built against the older `BattlepassState` shape
//   (`season_name` / `current_level` / `xp_current` / `xp_next_level` /
//   `next_reward_label` / `next_reward_type`) — kept for V1 to avoid
//   touching every consumer (cards, header, profil).
//
//   Mapping rules :
//     - `season_name`       ← `season.name`
//     - `current_level`     ← number of milestones whose `status === 'claimed'`
//     - `xp_current`        ← `cab_earned_season` (CAB doubles as XP proxy
//                              against the milestone ladder)
//     - `xp_next_level`     ← `cab_required` of the next non-claimed milestone
//                              (= first milestone with status in
//                              {`unlocked`, `locked`}). When all milestones
//                              are claimed, we fall back to the last
//                              milestone's `cab_required` (gauge stays full).
//     - `next_reward_label` ← human-readable label derived from `reward_type`
//                              + `reward_value` of the next milestone.
//     - `next_reward_type`  ← maps `cab` → `'cab'`, anything else → `'skin'`
//                              (V1 gift-card variants render with the skin
//                              tile artwork).
//
//   If the backend ships `{ season: null }` (no active season), we return
//   `null` so the card's skeleton stays put.

import { useQuery } from '@tanstack/react-query';
import { rewardsClient } from '@/services/rewards-client';
import type { BattlepassState } from '@/types/gamification';

/**
 * Raw backend milestone shape — kept private to this hook. Consumers should
 * use the mapped `BattlepassState` returned by `useBattlepass()`.
 */
interface BackendMilestone {
  id: string;
  milestone_number: number;
  cab_required: number;
  reward_type: string;
  reward_value: number;
  subscriber_only: boolean;
  status: 'claimed' | 'unlocked' | 'locked';
}

interface BackendBattlepassResponse {
  season: { id: string; name: string; ends_at: string } | null;
  cab_earned_season?: number;
  milestones?: BackendMilestone[];
}

/**
 * Build a human-readable French label for a milestone's reward.
 *
 * - `cab`       → "+N CAB"
 * - `gift_card` → "Carte cadeau {N}€" (V1 wording — provider brand surfaces
 *                  on claim)
 * - anything else → the raw reward_type uppercased as a safe fallback
 */
function buildRewardLabel(m: BackendMilestone | null): string {
  if (!m) return '';
  if (m.reward_type === 'cab') return `+${m.reward_value} CAB`;
  if (m.reward_type === 'gift_card') {
    // reward_value is in EUR for gift cards.
    return `Carte cadeau ${m.reward_value}€`;
  }
  return m.reward_type.toUpperCase();
}

/**
 * Map a backend `reward_type` into the narrow FE-side `next_reward_type`
 * enum. CAB rewards keep their identity ; everything else collapses to
 * `'skin'` (the FE renders generic reward tiles for the non-CAB tier).
 */
function mapRewardType(t: string | undefined): BattlepassState['next_reward_type'] {
  if (t === 'cab') return 'cab';
  if (!t) return null;
  return 'skin';
}

/**
 * Adapt the backend response into the legacy `BattlepassState` shape.
 *
 * Returns `null` when no active season is running (backend returns
 * `{ season: null }`) — the card renders its skeleton in that case.
 */
export function adaptBattlepassResponse(
  raw: BackendBattlepassResponse,
): BattlepassState | null {
  if (!raw.season) return null;
  const milestones = raw.milestones ?? [];
  const claimed = milestones.filter((m) => m.status === 'claimed').length;
  const next =
    milestones.find((m) => m.status !== 'claimed') ??
    (milestones.length > 0 ? milestones[milestones.length - 1] : null);

  // xp_next_level falls back to 1 to avoid a div-by-zero in the progress
  // bar denominator. The card itself clamps the value to [0,1].
  const xpNextLevel = next?.cab_required ?? 1;

  return {
    season_name: raw.season.name,
    current_level: claimed,
    xp_current: raw.cab_earned_season ?? 0,
    xp_next_level: xpNextLevel,
    next_reward_label: buildRewardLabel(next),
    next_reward_type: mapRewardType(next?.reward_type),
  };
}

export function useBattlepass() {
  return useQuery<BattlepassState | null>({
    queryKey: ['battlepass'],
    queryFn: async () => {
      const raw = await rewardsClient.get<BackendBattlepassResponse>(
        '/gamification/battlepass',
      );
      return adaptBattlepassResponse(raw);
    },
  });
}
