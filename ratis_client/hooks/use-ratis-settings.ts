// ratis_client/hooks/use-ratis-settings.ts
//
// V1.1 — runtime settings projection from the backend.
//
// Reads the whitelisted public subset of `app_settings` exposed by
// `GET /api/v1/rewards/settings/public`. The mobile UI uses these values
// for display-time derivations (jar prestige price, gift-card cap warning
// threshold, etc.) — see `services/public_settings_service.py` for the
// authoritative whitelist.
//
// 5 min stale time matches the backend Cache-Control max-age=300 ;
// settings change infrequently and we don't need real-time freshness.
//
// Keys are dotted paths (e.g. `pipeline.jar.monthly_subscription_price_cents`)
// so the response stays flat — callers index it directly without walking
// nested sections. Unknown keys (e.g. a new whitelist entry the API
// hasn't shipped yet) return `undefined` ; consumers must always provide
// a fallback via `??`.

import { useQuery } from '@tanstack/react-query';
import { rewardsClient } from '@/services/rewards-client';

/**
 * Flat dict of whitelisted runtime settings keyed by dotted path.
 *
 * Values are typed as `unknown` because the whitelist is heterogeneous
 * (number, string, array, …). Callers narrow with a type assertion at
 * read-time, paired with a fallback. Example :
 *
 * ```ts
 * const { data } = useRatisSettings();
 * const jarPriceCents =
 *   (data?.['pipeline.jar.monthly_subscription_price_cents'] as number | undefined) ??
 *   999;
 * ```
 */
export type RatisPublicSettings = Record<string, unknown>;

const SETTINGS_STALE_MS = 5 * 60_000;

export function useRatisSettings() {
  return useQuery<RatisPublicSettings>({
    queryKey: ['settings', 'public'],
    queryFn: () =>
      rewardsClient.get<RatisPublicSettings>('/rewards/settings/public'),
    staleTime: SETTINGS_STALE_MS,
  });
}
