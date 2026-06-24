// ratis_client/services/rewards-client.ts
//
// Thin client for ratis_rewards (cab / battlepass / missions / gift cards).
// The hooks (`use-cab-balance`, `use-shop-catalog`, …) consume `rewardsClient`
// directly via `.get<T>()` / `.post<T>()`. We re-export typed convenience
// wrappers for the shop endpoints so callers without a hook (e.g. legacy
// scripts or tests) can call them by name.
//
// All requests use bearer auth via the shared `api-client` factory (token
// injected by `tokenStorage`). Errors bubble as `AuthError` with `detail`
// populated from the FastAPI `{"detail": "..."}` envelope.

import { createApiClient } from '@/services/api-client';
import { requireEnv } from '@/services/env';
import type {
  ShopCatalogResponse,
  ShopOrderInput,
  ShopOrderResponse,
} from '@/types/shop';
import type {
  BufferMissionResponse,
  BurstClaimResponse,
  BurstLeaderboardAlltimeResponse,
  BurstLeaderboardMonthlyResponse,
  ClaimMissionResponse,
} from '@/types/gamification';
import type {
  SecretEventName,
  SecretEventResponse,
} from '@/types/achievements';

// Lazy thunk — see list-client.ts for rationale.
export const rewardsClient = createApiClient(
  () => requireEnv('EXPO_PUBLIC_REWARDS_API_URL', process.env.EXPO_PUBLIC_REWARDS_API_URL),
);

// -----------------------------------------------------------------------------
// Boutique V1 — typed wrappers
// -----------------------------------------------------------------------------

/** GET /rewards/gift-cards/catalog — active brands for the running season. */
export function getCatalog(): Promise<ShopCatalogResponse> {
  return rewardsClient.get<ShopCatalogResponse>('/rewards/gift-cards/catalog');
}

/**
 * POST /rewards/gift-cards/order — buy a gift card with CAB.
 *
 * The backend may return one of these `error.detail` codes which the UI
 * should translate via `shop.errors.*`:
 *   400 invalid_denomination · invalid_brand_id
 *   402 insufficient_cab_balance
 *   404 brand_not_available
 *   409 daily_redeem_cap_reached · weekly_redeem_cap_reached
 *       · annual_gift_card_cap_reached · duplicate_order_recent
 */
export function orderGiftCard(
  input: ShopOrderInput,
): Promise<ShopOrderResponse> {
  return rewardsClient.post<ShopOrderResponse>(
    '/rewards/gift-cards/order',
    input,
  );
}

// -----------------------------------------------------------------------------
// Buffer + Burst (refonte 2026-05-09 — replaces Stonks)
// -----------------------------------------------------------------------------

/**
 * POST /gamification/missions/{id}/buffer — apply 1 Buffer.
 *
 * Effects (atomic, server-side) :
 *   - buffer_count          += 1
 *   - target_count          *= 2
 *   - cab_reward             = R_original × (buffer_count + 1)
 *   - period_extended_until  = period_start + (buffer_count + 1) days
 *   - xp_reward              unchanged
 *
 * Backend `error.detail` codes the UI maps via `gamification.buffer.errors.*`:
 *   400 weekly_not_bufferable
 *   404 mission_not_found
 *   409 buffer_cap_reached · burst_locked · mission_not_pending
 */
export function applyBuffer(
  missionId: string,
): Promise<BufferMissionResponse> {
  return rewardsClient.post<BufferMissionResponse>(
    `/gamification/missions/${missionId}/buffer`,
  );
}

/**
 * POST /gamification/missions/{id}/claim — multi-claim cumulatif (Buffer-aware).
 *
 * Backend `error.detail` codes the UI maps via `gamification.claim.errors.*`:
 *   402 no_portion_available_now
 *   404 mission_not_found
 *   409 already_claimed
 *   410 mission_expired
 */
export function claimMission(
  missionId: string,
): Promise<ClaimMissionResponse> {
  return rewardsClient.post<ClaimMissionResponse>(
    `/gamification/missions/${missionId}/claim`,
  );
}

/**
 * POST /gamification/missions/{id}/burst-claim — claim Burst paliers (XP only).
 *
 * First claim flips `burst_locked = true` permanently (= no more Buffer).
 *
 * Backend `error.detail` codes the UI maps via `gamification.burst.errors.*`:
 *   402 no_burst_palier_unlocked
 *   404 mission_not_found
 */
export function claimBurst(missionId: string): Promise<BurstClaimResponse> {
  return rewardsClient.post<BurstClaimResponse>(
    `/gamification/missions/${missionId}/burst-claim`,
  );
}

/**
 * GET /gamification/leaderboard/burst-monthly
 *
 * @param month optional `YYYY-MM` string. Defaults to current month UTC server-side.
 */
export function getBurstLeaderboardMonthly(
  month?: string,
): Promise<BurstLeaderboardMonthlyResponse> {
  const qs = month ? `?month=${encodeURIComponent(month)}` : '';
  return rewardsClient.get<BurstLeaderboardMonthlyResponse>(
    `/gamification/leaderboard/burst-monthly${qs}`,
  );
}

/** GET /gamification/leaderboard/burst-alltime */
export function getBurstLeaderboardAlltime(): Promise<BurstLeaderboardAlltimeResponse> {
  return rewardsClient.get<BurstLeaderboardAlltimeResponse>(
    '/gamification/leaderboard/burst-alltime',
  );
}

// -----------------------------------------------------------------------------
// Achievements V1 (PR 8/8)
// -----------------------------------------------------------------------------

/**
 * POST /rewards/achievements/secret-event — fire a secret event the
 * dispatcher may turn into an unlock. Always fire-and-forget at the call
 * site (`.catch(() => {})`) — the unlock decision belongs to the backend
 * and the FE does not need the response synchronously.
 *
 * Rate-limited 10/h/user server-side. Whitelist of event names is enforced
 * at the Pydantic layer (Literal) → unknown values 422 before the counter
 * decrements.
 */
export function triggerSecretEvent(
  event: SecretEventName,
): Promise<SecretEventResponse> {
  return rewardsClient.post<SecretEventResponse>(
    '/rewards/achievements/secret-event',
    { event },
  );
}
