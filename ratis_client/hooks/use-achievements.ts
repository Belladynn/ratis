// ratis_client/hooks/use-achievements.ts
//
// Achievements V1 — React Query hooks (PR 8/8).
//
// Two read-only queries against the public endpoints under
// `/api/v1/rewards/achievements*` (cf
// docs/superpowers/specs/2026-05-09-achievements-v1-design.md
// § "Endpoints publics") :
//
//   - `useAchievements({ category?, unlocked? })` — full catalog grouped by
//     the 8 display buckets (7 catalog categories + computed `j_y_etais`).
//   - `useAchievementDetail(id)` — single achievement detail. Disabled when
//     `id` is null/undefined so screens can pass the modal-open state
//     directly without conditional hooking.
//
// `staleTime: 60_000` matches the backend `Cache-Control: private, max-age=60`
// hint — no point re-fetching faster than the server expects.

import { useQuery } from '@tanstack/react-query';

import { rewardsClient } from '@/services/rewards-client';
import type {
  AchievementItem,
  AchievementsListResponse,
} from '@/types/achievements';

const ACHIEVEMENTS_STALE_MS = 60_000;

export interface UseAchievementsOptions {
  /** Filter to a single category bucket (e.g. `'volume'`, `'j_y_etais'`). */
  category?: string;
  /** `'true'` → unlocked only. `'false'` → locked only. Omit for both. */
  unlocked?: 'true' | 'false';
}

/**
 * Build the query string the backend expects. Returns `''` when no filter
 * is set so the URL stays cache-friendly (`/rewards/achievements` is the
 * canonical key for "everything").
 */
function buildQueryString(opts?: UseAchievementsOptions): string {
  if (!opts) return '';
  const params = new URLSearchParams();
  if (opts.category) params.set('category', opts.category);
  if (opts.unlocked) params.set('unlocked', opts.unlocked);
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

/**
 * GET /rewards/achievements — full catalog grouped by category.
 *
 * The query key includes the optional filter values so React Query keeps
 * separate caches per filter combo. Set `staleTime` to 60s so back-to-back
 * mounts (e.g. open modal → close → re-open) don't re-fetch.
 */
export function useAchievements(opts?: UseAchievementsOptions) {
  return useQuery<AchievementsListResponse>({
    queryKey: ['achievements', opts?.category ?? null, opts?.unlocked ?? null],
    queryFn: () =>
      rewardsClient.get<AchievementsListResponse>(
        `/rewards/achievements${buildQueryString(opts)}`,
      ),
    staleTime: ACHIEVEMENTS_STALE_MS,
  });
}

/**
 * GET /rewards/achievements/{id} — detail view (used by the celebration
 * modal & the bottom-sheet). Disabled when `id` is null/undefined so
 * callers can pass `modalOpen ? selectedId : null` directly.
 */
export function useAchievementDetail(id: string | null | undefined) {
  return useQuery<AchievementItem>({
    queryKey: ['achievement', id],
    queryFn: () =>
      rewardsClient.get<AchievementItem>(`/rewards/achievements/${id}`),
    enabled: !!id,
    staleTime: ACHIEVEMENTS_STALE_MS,
  });
}
